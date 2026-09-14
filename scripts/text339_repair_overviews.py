"""Correct three cross-cache fallback references before labels or embeddings."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
import pandas as pd
from text339_common import ROOT,OUT,require,pin,write_json
from text339_text import english_cache,validate_cache,text_hash

def run():
    require(not (OUT/'overview-repair.json').exists(),'preserve completed repair')
    report=json.loads((OUT/'text-source-report.json').read_text())
    for name,expected in report['files'].items():require(pin(OUT/name)==expected,'pre-repair source artifact')
    frame=pd.read_parquet(OUT/'texts.parquet');qa=pd.read_parquet(OUT/'quality-sample.parquet');ledger=pd.read_parquet(OUT/'overview-fallback-ledger.parquet')
    empty=frame.index[frame.overview.eq('')]
    require(frame.loc[empty,'movie_id'].tolist()==[137813,213187,266856],'only the three independently identified missing fallbacks')
    changed=[];entries=[]
    for i in empty:
        row=frame.loc[i];primary=ROOT/row.overview_source;body=json.loads(primary.read_text())['body']
        path=english_cache(primary,int(row.tmdb_id));require(path is not None,'English source exists')
        cache=json.loads(path.read_text());validate_cache(cache,int(row.tmdb_id),'en-US')
        require(not body.get('imdb_id') or not cache['body'].get('imdb_id') or body['imdb_id']==cache['body']['imdb_id'],'cross-cache IMDb consistency')
        overview=(cache['body'].get('overview') or '').strip();require(bool(overview),'nonempty replacement text')
        updates={'overview':overview,'overview_language':'en','overview_source':path.relative_to(ROOT).as_posix(),'overview_sha256':text_hash(overview)}
        for key,value in updates.items():
            frame.loc[i,key]=value
            qa.loc[qa.movie_id.eq(row.movie_id),key]=value
        entries.append({'path':updates['overview_source'],'request_language':'en-US','body_sha256':cache['body_sha256'],**pin(path)})
        changed.append({'movie_id':int(row.movie_id),'tmdb_id':int(row.tmdb_id),'new_text_characters':len(overview),'text_hash':text_hash(overview),'cache':entries[-1]})
    names=['texts.parquet','quality-sample.parquet','overview-fallback-ledger.parquet','text-source-report.json','preflight.json','catalog.parquet']
    backup=OUT/'before-overview-fallback-fix';require(not backup.exists(),'preserve pre-repair snapshot');backup.mkdir()
    for name in names:
        if (OUT/name).exists():shutil.copy2(OUT/name,backup/name)
    old=pd.read_parquet(backup/'texts.parquet');fixed_columns=['overview','overview_language','overview_source','overview_sha256']
    require(old.drop(columns=fixed_columns).equals(frame.drop(columns=fixed_columns)),'all Wiki, identity, release and other fields unchanged')
    require(old.loc[~old.index.isin(empty)].equals(frame.loc[~frame.index.isin(empty)]),'all other movie rows unchanged')
    frame.to_parquet(OUT/'texts.parquet',index=False);qa.to_parquet(OUT/'quality-sample.parquet',index=False)
    pd.concat([ledger,pd.DataFrame(entries)],ignore_index=True).to_parquet(OUT/'overview-fallback-ledger.parquet',index=False)
    report['overview_nonempty']=int(frame.overview.ne('').sum());report['initial_source_code']=report.pop('source_code')
    report['repair_code']=pin(__file__);report['repair_helper']=pin(ROOT/'scripts/text339_text.py')
    report['files']={name:pin(OUT/name) for name in ['texts.parquet','quality-sample.parquet','overview-fallback-ledger.parquet']}
    write_json(OUT/'text-source-report.json',report)
    write_json(OUT/'overview-repair.json',{'changes':changed,'code':pin(__file__),'before':{n:pin(backup/n) for n in names if (backup/n).exists()},
                                        'after':report['files'],'all_other_columns_and_rows_unchanged':True,'rating_values_read':0})
    # The old preflight refers to the old text hash; keep it and allow a fresh label-free preflight.
    if (OUT/'preflight.json').exists():
        target=OUT/'preflight-before-overview-fix.json';require(not target.exists(),'preserve old preflight reference')
        (OUT/'preflight.json').rename(target)
    print(json.dumps({'overview_nonempty':report['overview_nonempty'],'changes':changed}),flush=True)

if __name__=='__main__':run()
