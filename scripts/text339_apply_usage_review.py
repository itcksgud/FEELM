"""Apply label-blind semantic QA decisions equally to both Wiki conditions."""
from __future__ import annotations
import json
import shutil
import pandas as pd
from text339_common import ROOT, DOC, OUT, require, pin, write_json
from text339_text import text_hash


def run():
    manifest_path=DOC/'wiki-usage-decisions.json'
    decision=json.loads(manifest_path.read_text(encoding='utf-8'))
    require(decision['status']=='REVIEWED_BEFORE_EMBEDDING','semantic decision gate')
    require(pin(OUT/'texts.parquet')==decision['texts_before'],'reviewed source identity')
    require(not (OUT/'embedding-seal.json').exists() and not (OUT/'encoding-progress.json').exists(),'before encoding')
    require(not (OUT/'wiki-usage-application.json').exists(),'preserve applied usage review')
    report=json.loads((OUT/'text-source-report.json').read_text(encoding='utf-8'))
    for name,expected in report['files'].items():require(pin(OUT/name)==expected,'source artifact drift')
    frame=pd.read_parquet(OUT/'texts.parquet');qa=pd.read_parquet(OUT/'quality-sample.parquet')
    old=frame.copy(deep=True);changes=decision['quarantine']
    ids=[d['movie_id'] for d in changes]
    require(len(ids)==len(set(ids)) and set(ids)<=set(frame.movie_id),'unique known quarantine movies')
    altered=['T2','T3','T2_sha256','T3_sha256','exclusion_reason','restored_body','changed_nonempty']
    before_rows=[]
    for d in changes:
        mask=frame.movie_id.eq(d['movie_id']);row=frame.loc[mask].iloc[0]
        require(row.qid==d['qid'] and row.T2_sha256==d['T2_sha256'] and row.T3_sha256==d['T3_sha256'],'exact reviewed Wiki version')
        require(row.exclusion_reason=='' and bool(row.T2 or row.T3),'only currently eligible Wiki sources')
        require(d['reason'] in ['NON_PLOT_PROSE','ENTITY_SCOPE_PENDING','UNCERTAIN_NARRATIVE_SCOPE'] and bool(d['evidence']),'documented usage reason')
        before_rows.append({'movie_id':int(row.movie_id),'qid':row.qid,**{c:row[c] for c in altered}})
        updates={'T2':'','T3':'','T2_sha256':text_hash(''),'T3_sha256':text_hash(''),
                 'exclusion_reason':'SEMANTIC_QA_'+d['reason'],'restored_body':False,'changed_nonempty':False}
        for col,value in updates.items():
            frame.loc[mask,col]=value
            qa.loc[qa.movie_id.eq(d['movie_id']),col]=value
    require(old.drop(columns=altered).equals(frame.drop(columns=altered)),'identity/raw/overview/catalog unchanged')
    require(old.loc[~old.movie_id.isin(ids)].equals(frame.loc[~frame.movie_id.isin(ids)]),'unreviewed movie rows unchanged')
    # Preserve the original broad sample and expose the complete changed/restored review population.
    reviewed_ids=decision['changed_population_movie_ids']
    actual_ids=old.loc[old.changed_nonempty|old.restored_body,'movie_id'].tolist()
    require(sorted(reviewed_ids)==sorted(actual_ids) and len(reviewed_ids)==len(set(reviewed_ids)),'all changed and restored texts reviewed')
    backup=OUT/'before-wiki-usage-review';require(not backup.exists(),'preserve pre-QA materialization');backup.mkdir()
    names=['texts.parquet','quality-sample.parquet','text-source-report.json','preflight.json','catalog.parquet']
    for name in names:
        if (OUT/name).exists():shutil.copy2(OUT/name,backup/name)
    frame.to_parquet(OUT/'texts.parquet',index=False);qa.to_parquet(OUT/'quality-sample.parquet',index=False)
    frame[frame.movie_id.isin(reviewed_ids)].to_parquet(OUT/'changed-text-quality-population.parquet',index=False)
    pd.DataFrame(before_rows).to_parquet(OUT/'wiki-quarantined-before.parquet',index=False)
    report.update(T2_nonempty=int(frame.T2.ne('').sum()),T3_nonempty=int(frame.T3.ne('').sum()),
                  restored_body=int(frame.restored_body.sum()),changed_nonempty=int(frame.changed_nonempty.sum()),
                  exclusion_reasons=frame.exclusion_reason.value_counts().to_dict(),
                  changed_population_reviewed=len(reviewed_ids),semantic_quarantine=len(ids),
                  usage_review={'manifest':pin(manifest_path),'application_code':pin(__file__)})
    report['files'].update({n:pin(OUT/n) for n in ['texts.parquet','quality-sample.parquet','changed-text-quality-population.parquet','wiki-quarantined-before.parquet']})
    write_json(OUT/'text-source-report.json',report)
    write_json(OUT/'wiki-usage-application.json',{'code':pin(__file__),'manifest':pin(manifest_path),
        'before':{n:pin(backup/n) for n in names if (backup/n).exists()},'after':report['files'],
        'movie_ids':ids,'other_rows_and_fields_unchanged':True,'rating_values_read':0})
    if (OUT/'preflight.json').exists():
        target=OUT/'preflight-before-wiki-usage-review.json';require(not target.exists(),'preserve preflight')
        (OUT/'preflight.json').rename(target)
    print(json.dumps({'quarantined':len(ids),'changed_population':len(reviewed_ids),
                     'T2_nonempty':report['T2_nonempty'],'T3_nonempty':report['T3_nonempty']}),flush=True)


if __name__=='__main__':run()
