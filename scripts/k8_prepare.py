"""Stream full service source metadata; validate every identity. No training."""
import argparse
from collections import Counter
import io
import json
from pathlib import Path
import tarfile
import zipfile
import numpy as np
import pandas as pd
from k8_common import DOC,OUT,Guard,pin,read,write,reviewed,seal

def prepare(cfg):
    source=Path(cfg['snapshot']); dest=OUT/'prepare';dest.mkdir(parents=True,exist_ok=True)
    assert not (dest/'catalog.parquet').exists(),'preserve completed preparation'
    pins={p.name:pin(p) for p in sorted(source.iterdir()) if p.is_file()}
    assert pins['movie-id-mapping-run-1_20260910.zip']['sha256']=='551201d34f319c2ae6e96d623c6ad2303047d9d38b9daf3c8fd38a6ea4b89a24'
    for line in (source/'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines():
        digest,name=line.strip().split(maxsplit=1)
        name=Path(name.lstrip('*')).name
        if name in pins:assert pins[name]['sha256']==digest,('published raw digest',name)
    assert pins['02_tmdb-movie-raw_20231013-20260907.tar.gz']['sha256']=='cb4a9f119bdc349841f9245723f24f911f823f65e25bf4ce08ae03fce335e10e'
    assert pins['03_tmdb-keywords-raw_20231013-20260907.tar.gz']['sha256']=='e6681572435284c721425ea1d8b31623cbb0f75804aafcb2fa370eb4bd4b7326'
    with zipfile.ZipFile(source/'postgresql-final_20260909.zip') as z:
        data=z.read('service-movies.csv')
        import hashlib
        assert hashlib.sha256(data).hexdigest()=='43805f3fa99457726de6dd748195adc32ccd1c400c8d9f891d0b2bad039f4408'
        service=pd.read_csv(io.BytesIO(data))
    assert len(service)==cfg['expected_movies']
    assert service.service_movie_id.is_unique and service.tmdb_id.is_unique and not service.isna().any().any()
    service=service.sort_values('service_movie_id').reset_index(drop=True)
    ids=set(service.tmdb_id.astype(int)); rows={}; genre_names={};keyword_names={}
    with tarfile.open(source/'02_tmdb-movie-raw_20231013-20260907.tar.gz','r|gz') as tf:
        for m in tf:
            if not m.isfile() or not m.name.endswith('.json'):continue
            key=int(Path(m.name).stem);v=json.load(tf.extractfile(m));d=v['details']
            assert key==d['id'] and key in ids and key not in rows,('body identity',key)
            genres=d.get('genres') or []
            for g in genres:genre_names[str(g['id'])]=g['name']
            rows[key]={'tmdb_id':key,'title':d.get('title') or d.get('original_title') or '',
                       'original_title':d.get('original_title') or '',
                       'genre_ids':sorted(set(int(g['id']) for g in genres)),
                       'overview':str(d.get('overview') or '').strip(),
                       'original_language':d.get('original_language') or '',
                       'release_date':d.get('release_date') or '',
                       'status':d.get('status') or '', 'adult':bool(d.get('adult')),'video':bool(d.get('video')),
                       'vote_average':float(d.get('vote_average') or 0),'vote_count':int(d.get('vote_count') or 0),
                       'popularity':float(d.get('popularity') or 0),
                       'keyword_ids':[],'keyword_file_present':False}
            if len(rows)%50000==0:print('RAW',len(rows),flush=True)
    assert set(rows)==ids,'full body ID set'
    seen=set()
    with tarfile.open(source/'03_tmdb-keywords-raw_20231013-20260907.tar.gz','r|gz') as tf:
        for m in tf:
            if not m.isfile() or not m.name.endswith('.json'):continue
            key=int(Path(m.name).stem);v=json.load(tf.extractfile(m))
            assert key==v['id'] and key in ids and key not in seen,('keyword identity',key)
            seen.add(key);kw=v['keywords']
            rows[key]['keyword_file_present']=True
            rows[key]['keyword_ids']=sorted(set(int(k['id']) for k in kw))
            for k in kw:keyword_names[str(k['id'])]=k['name']
    frame=service.merge(pd.DataFrame(rows.values()),on='tmdb_id',validate='one_to_one')
    with zipfile.ZipFile(source/'movie-id-mapping-run-1_20260910.zip') as z:
        parts=sorted(n for n in z.namelist() if n.startswith('run-1/service-movie-id-map/') and n.endswith('.parquet'))
        assert len(parts)==128
        mapping=pd.concat([pd.read_parquet(io.BytesIO(z.read(n))) for n in parts],ignore_index=True)
    assert len(mapping)==len(frame) and mapping.service_movie_id.is_unique
    frame=frame.merge(mapping,on=['service_movie_id','tmdb_id'],validate='one_to_one')
    assert len(frame)==cfg['expected_movies'] and not frame.mapping_status.isna().any()
    matched=frame.mapping_status.eq('MATCHED')
    assert frame.loc[matched,'movielens_movie_id'].notna().all()
    assert frame.loc[matched,'movielens_movie_id'].is_unique,'ML mapping must be unambiguous for interactions'
    frame=frame.sort_values('service_movie_id').reset_index(drop=True)
    frame['genre_present']=frame.genre_ids.map(len)>0
    frame['keyword_present']=frame.keyword_ids.map(len)>0
    frame['overview_present']=frame.overview.str.len()>0
    frame['no_content']=~(frame.genre_present|frame.keyword_present|frame.overview_present)
    frame.to_parquet(dest/'catalog.parquet',index=False)
    write(dest/'names.json',{'genres':genre_names,'keywords':keyword_names})
    report={'rows':len(frame),'all_body_ids_verified':len(rows),'keyword_body_ids_verified':len(seen),
            'keyword_missing_files':len(ids-seen),'keyword_missing_tmdb_ids':sorted(ids-seen),
            'mapping_status':frame.mapping_status.value_counts().to_dict(),
            'availability':{c:int(frame[c].sum()) for c in ['genre_present','keyword_present','overview_present','no_content']},
            'neither_overview_nor_keyword':int((~frame.overview_present&~frame.keyword_present).sum()),
            'adult_or_video':int((frame.adult|frame.video).sum()),
            'raw_sources':pins}
    write(dest/'report.json',report);seal('prepare');print(json.dumps({k:v for k,v in report.items() if k!='raw_sources'}),flush=True)

if __name__=='__main__':
    cfg=read(DOC/'config.json');reviewed('prepare')
    with Guard(cfg,'prepare'):prepare(cfg)
