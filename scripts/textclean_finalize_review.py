"""Combine actual independent reviews conservatively before any text is applied."""
import json
import re
import pandas as pd
from textclean_source import ROOT,OLD,OUT,DOC
from rec046_common import require,pin,write_json

def main():
    require(not (DOC/'semantic-review.json').exists(),'preserve semantic decision')
    candidates=[json.loads(s) for s in (OUT/'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    proposal_names=['proposal-0.json','proposal-1-final.json','proposal-2.json']
    preview_seal=json.loads((OUT/'preview-seal-v2.json').read_text())
    require(preview_seal['candidates']==pin(OUT/'candidates.jsonl') and preview_seal['original']==pin(OLD/'texts.parquet'),'actual preview source parents')
    require(preview_seal['preview']==pin(OUT/'review-preview-v2.parquet'),'actual preview body')
    require(set(preview_seal['proposals'])==set(proposal_names),'exact primary review set')
    for name in proposal_names:require(preview_seal['proposals'][name]==pin(DOC/name),'actual primary proposal parent')
    require(preview_seal['code']==pin(ROOT/'scripts/textclean_review_preview.py'),'preview generation code')
    proposals=[json.loads((DOC/n).read_text()) for n in proposal_names]
    proposed={};primary={}
    for i,p in enumerate(proposals):
        values=sum(p['groups'].values(),[]);require(len(values)==len(set(values))==p['proposed'],'unique proposals')
        proposed[i]=set(values)
        for n in values:
            require(candidates[n]['partition']==i and candidates[n]['ordinal']==n,'assigned original ordinal');primary[n]=p['reviewer']
    require(sum(p['read'] for p in proposals)==len(candidates),'every candidate read')
    preview=pd.read_parquet(OUT/'review-preview-v2.parquet')
    docs0=preview[preview.ordinals.map(lambda v:bool(set(v)&proposed[0]))]
    require(len(docs0)==181,'split review document axis')
    assigned={
      'second-review-0-head.json':set(n for v in docs0.iloc[:91].ordinals for n in v)&proposed[0],
      'second-review-0-tail.json':set(n for v in docs0.iloc[91:].ordinals for n in v)&proposed[0],
      'second-review-1.json':proposed[1],
      'second-review-2.json':proposed[2],
    }
    require(len(assigned['second-review-0-head.json'])==110 and len(assigned['second-review-0-tail.json'])==99,'exact reviewer split')
    secondary={};veto=set();reviews={}
    for name,values in assigned.items():
        r=json.loads((DOC/name).read_text());reviews[name]=r
        require(r['status']=='COMPLETE' and r['all_other_assigned_proposals']=='APPROVE','completed second review')
        require(r['preview']==pin(OUT/'review-preview-v2.parquet') and r['preview_seal']==pin(OUT/'preview-seal-v2.json'),'same complete context preview')
        partition=0 if '-0-' in name else int(name.split('-')[-1].split('.')[0])
        require(r['proposal']==pin(DOC/proposal_names[partition]),'actual reviewed proposal')
        reject=set(r['rejected_ordinals']);require(reject<=values and r['read_spans']==len(values),'all assigned spans reviewed')
        require(r['approved_count']==len(values)-len(reject),'approval count')
        for n in values:
            require(n not in secondary and primary[n]!=r['reviewer'],'independent unique second reviewer');secondary[n]=r['reviewer']
        veto|=reject|set(r.get('cross_partition_retain',[]))
    require(set(secondary)==set(primary),'complete two-review coverage')
    correction=json.loads((DOC/'post-application-veto.json').read_text())
    require(correction['decision']=='RETAIN_ORIGINAL' and correction['audit']==pin(DOC/'source-audit-attempt-1.json'),'actual post-application semantic audit')
    require(correction['initial_clean_seal']==pin(OUT/'source-attempt-1/clean-seal.json'),'preserved initial source attempt')
    veto|=set(correction['veto_ordinals'])
    screen=json.loads((DOC/'safety-screen-proposal.json').read_text())
    pattern=json.loads((DOC/'safety-pattern.json').read_text())
    screen_review=json.loads((DOC/'safety-screen-review.json').read_text())
    require(screen_review['status']=='PASS' and screen_review['proposal']==pin(DOC/'safety-screen-proposal.json') and screen_review['pattern']==pin(DOC/'safety-pattern.json'),'independent retention screen review')
    require(screen['pattern_file']==pin(DOC/'safety-pattern.json') and screen['candidates']==pin(OUT/'candidates.jsonl'),'actual retention screen source')
    before=set(primary)-veto
    matches=sorted(n for n in before if re.search(pattern['pattern'],candidates[n]['span'],re.I))
    require(matches==screen['veto_ordinals'] and len(before)==screen['approved_before'] and len(before)-len(matches)==screen['approved_after'],'retention screen reproducible and deletion monotonic')
    veto|=set(matches)
    require(veto<=set(range(len(candidates))),'valid veto IDs')
    approved=sorted(set(primary)-veto);qa=json.loads((DOC/'qa64-review.json').read_text())
    require(qa['status']=='PASS_INITIAL_QA' and qa['total_read']==64 and qa['sample']==pin(OUT/'qa64.json'),'fixed source QA completed')
    files=proposal_names+list(assigned)+['qa64-review.json','post-application-veto.json','source-audit-attempt-1.json','safety-pattern.json','safety-screen-proposal.json','safety-screen-review.json']
    result={'status':'PASS','scope':'Narrow conservative sentence removal, no complete semantic extraction claim',
      'candidates':pin(OUT/'candidates.jsonl'),'original':pin(OLD/'texts.parquet'),'preview':pin(OUT/'review-preview-v2.parquet'),
      'qa64_status':'PASS','full_document_second_review':True,'candidate_count':len(candidates),'primary_proposed_count':len(primary),
      'vetoed_proposed_count':len(set(primary)&veto),'approved_remove_ids':[candidates[n]['id'] for n in approved],
      'approved_remove_ordinals':approved,'retained_proposed_ordinals':sorted(set(primary)&veto),
      'approvals':{candidates[n]['id']:[primary[n],secondary[n]] for n in approved},
      'review_files':{n:pin(DOC/n) for n in files},'finalization_code':pin(__file__),
      'rating_values_read':0,'new_recommender_predictions_read':0}
    result['inherited_source_flags']='Original quote-restoration/exclusion flags in texts.parquet describe the reference source; semantic changes are in the deletion ledger.'
    write_json(DOC/'semantic-review.json',result);print('FINAL SEMANTIC',len(primary),len(approved),len(set(primary)&veto),flush=True)

if __name__=='__main__':main()
