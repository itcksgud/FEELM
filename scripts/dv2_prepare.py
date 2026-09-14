"""Audit original public TMDb evidence without coercing unknowns to zero."""
import json
import math
from pathlib import Path
import pickle
import tarfile
import platform
import numpy as np
import pandas as pd
import sklearn  # Load OpenMP before entering the explicit two-thread scope.
from threadpoolctl import threadpool_limits,threadpool_info
from dv2_common import *

def field(d,key,integer=False,max_value=None):
    if key not in d:return 'MISSING',None
    v=d[key]
    if v is None:return 'NULL',None
    if isinstance(v,bool) or not isinstance(v,(int,float)):return 'NON_NUMERIC',None
    if not math.isfinite(v):return 'NON_FINITE',None
    if v<0 or (max_value is not None and v>max_value):return 'OUT_OF_RANGE',float(v)
    if integer and int(v)!=v:return 'NON_INTEGER',float(v)
    return 'ZERO' if v==0 else 'POSITIVE',float(v)

def quality_state(rs,rv,vs,vv):
    if rs not in ['ZERO','POSITIVE']:return 'R_'+rs
    if vs not in ['ZERO','POSITIVE']:return 'V_'+vs
    if vv==0:return 'CONTRADICTORY_ZERO_COUNT' if rv>0 else 'ZERO_VOTES'
    if rv==0:return 'AMBIGUOUS_ZERO_AVERAGE'
    return 'VALID'

def positive(d,key,allow_zero=False):
    state,value=field(d,key)
    return value if state=='POSITIVE' or (allow_zero and state=='ZERO') else None

def ids(records,key='id',limit=None):
    result=[]
    for r in records or []:
        v=r.get(key)
        if isinstance(v,int) and not isinstance(v,bool) and v>0 and v not in result:result.append(v)
        if limit is not None and len(result)>=limit:break
    return result

def normalize_raw(v,member):
    d=v['details'];credits=v.get('credits') or {}
    rs,rv=field(d,'vote_average',max_value=10);vs,vv=field(d,'vote_count',integer=True)
    cast=sorted(credits.get('cast') or [],key=lambda c:(c.get('order',1000000),c.get('id',1000000)))
    release=d.get('release_date') or ''
    try:year=int(release[:4]) if len(release)>=4 else None
    except ValueError:year=None
    collection=d.get('belongs_to_collection')
    stamps={k:val for k,val in v.items() if any(s in k.lower() for s in ['fetch','time','date'])}
    boolstate=lambda k: 'MISSING' if k not in d else 'NULL' if d[k] is None else 'FALSE' if d[k] is False else 'TRUE' if d[k] is True else 'INVALID'
    return {'tmdb_id':d['id'],'raw_vote_average_state':rs,'raw_vote_count_state':vs,
            'raw_adult_state':boolstate('adult'),'raw_video_state':boolstate('video'),
            'raw_vote_average_json':json.dumps(d.get('vote_average'),ensure_ascii=False),
            'raw_vote_count_json':json.dumps(d.get('vote_count'),ensure_ascii=False),
            'raw_vote_average_number':rv,'raw_vote_count_number':vv,'quality_state':quality_state(rs,rv,vs,vv),
            'raw_source_member':member.name,'raw_tar_mtime':member.mtime,'raw_timestamp_fields':json.dumps(stamps,ensure_ascii=False),
            'production_country_codes':sorted({str(c['iso_3166_1']) for c in d.get('production_countries') or [] if c.get('iso_3166_1')}),
            'origin_country_codes':d.get('origin_country') or [],
            'director_ids':ids([c for c in credits.get('crew') or [] if c.get('job')=='Director']),
            'top5_cast_ids':ids(cast,limit=5),'production_company_ids':ids(d.get('production_companies')),
            'collection_ids':ids([collection]) if isinstance(collection,dict) else [],
            'release_year':year,'runtime_minutes':positive(d,'runtime'),
            'tmdb_vote_average':positive(d,'vote_average'),'tmdb_vote_count':positive(d,'vote_count',True),
            'tmdb_popularity':positive(d,'popularity',True),'raw_credits_present':'credits' in v}

def prepare():
    dest=require_fresh('prepare','report.json')
    p=OLDOUT/'prepare/catalog.parquet'
    assert pin(p)['sha256']=='f16abeb51ccff940a73480aa07347a44502524c82f29d9b7f1fde379b844006f'
    frame=pd.read_parquet(p);assert len(frame)==CFG['expected_movies']
    assert frame.service_movie_id.is_unique and frame.tmdb_id.is_unique and np.all(np.diff(frame.service_movie_id)>0)
    rawpath=Path(CFG['snapshot'])/'02_tmdb-movie-raw_20231013-20260907.tar.gz'
    assert pin(rawpath)['sha256']=='cb4a9f119bdc349841f9245723f24f911f823f65e25bf4ce08ae03fce335e10e'
    keywordpath=Path(CFG['snapshot'])/'03_tmdb-keywords-raw_20231013-20260907.tar.gz'
    assert pin(keywordpath)['sha256']=='e6681572435284c721425ea1d8b31623cbb0f75804aafcb2fa370eb4bd4b7326'
    rows=[];seen=set();axis=set(frame.tmdb_id.astype(int))
    with tarfile.open(rawpath,'r|gz') as tf:
        for member in tf:
            if not member.isfile() or not member.name.endswith('.json'):continue
            v=json.load(tf.extractfile(member));d=v['details'];key=int(Path(member.name).stem)
            assert key==d['id'] and key in axis and key not in seen
            assert not v.get('credits') or v['credits'].get('id',key)==key
            seen.add(key);rows.append(normalize_raw(v,member))
            if len(rows)%50000==0:print('RAW_EVIDENCE',len(rows),flush=True)
    assert seen==axis
    frame=frame.merge(pd.DataFrame(rows),on='tmdb_id',validate='one_to_one').sort_values('service_movie_id').reset_index(drop=True)
    bundle=frozen_bundle();blocks,g=transform(frame,bundle['preprocessor']);x=combine(blocks,CFG['content_weights'])
    top=nearest(combine(blocks,bundle['top_weights']),bundle['top_centers'])[0]
    oldassign=pd.read_parquet(OLDOUT/'final/assignments.parquet')
    assert pin(OLDOUT/'final/assignments.parquet')['sha256']=='095c157daa77ffdd6d09fd4f33341b51bed81890f57095492112b5ef75a54708'
    assert np.array_equal(frame.service_movie_id,oldassign.service_movie_id) and np.array_equal(top,oldassign.taste_id)
    frame['taste_id']=top
    frame.to_parquet(dest/'catalog.parquet',index=False)
    np.save(dest/'content.npy',x);np.save(dest/'genre.npy',blocks[0]);np.save(dest/'genre-binary.npy',g);np.save(dest/'top.npy',top)
    base=eligibility(frame,x);np.save(dest/'base-eligible.npy',base)
    q,order,qinfo=quality(frame,base);np.save(dest/'quality.npy',q);np.save(dest/'quality-order.npy',order)
    write(dest/'quality-config.json',qinfo)
    # Reuse fully reconstructed viewed/ratings contexts; no v1 policy, rank or predictor reused.
    contexts=OLDOUT/'recommend/contexts.json';audit=OLDOUT/'recommend/interaction-source-audit.json'
    assert pin(OLDOUT/'recommend-seal.json')['sha256']=='2f83b17ef6a1959814354105f1d7a10ab85c9d6499e37b17d77bb13e62681ccd'
    oldseal=read(OLDOUT/'recommend-seal.json')['files']
    for path in [contexts,audit,OLDOUT/'recommend/roles.json']:assert pin(path)==oldseal[path.relative_to(OLDOUT).as_posix()]
    cc=read(contexts);assert len({c['uid'] for c in cc})==270
    original_path=Path(CFG['reference'])/'text339/contexts.json'
    assert pin(original_path)==read(audit)['sources']['text339/contexts.json']
    original=read(original_path);lookup={(c['uid'],c['cap']):c for c in original}
    assert len(lookup)==len(original)==len(cc)
    for c in cc:
        assert set(c['history'])<=set(c['viewed']) and not set(c['target'])&set(c['viewed'])
        source=lookup[c['uid'],c['cap']]
        assert len(source['oi'])==len(source['stars'])==len(source['input_timestamps']) and len(source['oi'])<=c['cap']
        assert max(source['input_timestamps'],default=0)<CFG['history_origin']
        assert all(CFG['history_origin']<=t<CFG['history_origin']+180*86400 for t in source['target_timestamps'])
        c['original_input_count']=len(source['oi']);c['original_stars']=source['stars'];c['original_input_timestamps']=source['input_timestamps']
    write(dest/'contexts.json',cc);oldroles=read(OLDOUT/'recommend/roles.json')
    role_path=Path(CFG['reference'])/CFG['role_source']
    assert pin(role_path)['sha256']==CFG['role_source_sha256']
    role_frame=pd.read_csv(role_path)
    assert role_frame.uid.is_unique and len(role_frame)==270 and set(role_frame.role)=={'calibration','comparison'}
    roles={'validation':role_frame.loc[role_frame.role.eq('calibration'),'uid'].astype(int).tolist(),
           'verification':role_frame.loc[role_frame.role.eq('comparison'),'uid'].astype(int).tolist(),
           'scope':'REUSED_DEVELOPMENT_ALIGNED_WITH_EXISTING_PREDICTOR_CALIBRATION',
           'v1_check_overlap_with_calibration':len(set(oldroles['verification'])&set(role_frame.loc[role_frame.role.eq('calibration'),'uid']))}
    assert len(roles['validation'])==90 and len(roles['verification'])==180 and roles['v1_check_overlap_with_calibration']==57
    assert not set(roles['validation'])&set(roles['verification'])
    assert set(roles['validation'])|set(roles['verification'])=={c['uid'] for c in cc}
    write(dest/'roles.json',roles)
    sensitivity=[]
    for kind in ['movie_mean','vote_mean']:
        for m in CFG['quality_m_sensitivity']:
            qq,oo,info=quality(frame,base,kind,m)
            for n in [10,100,1000,10000]:
                z=frame.iloc[oo[:n]];sensitivity.append({**info,'prefix':n,'actual':len(z),'low20_count':int((z.raw_vote_count_number<=20).sum()),'ko_count':int(z.original_language.eq('ko').sum()),'kr_count':int(z.production_country_codes.map(lambda a:'KR' in a).sum()),'recent2024_count':int((z.release_year>=2024).sum()),'service_ids':z.service_movie_id.iloc[:10].tolist()})
    write(dest/'quality-sensitivity.json',sensitivity)
    report={'rows':len(frame),'all_top_assignments_identical':True,'quality_states':frame.quality_state.value_counts().to_dict(),
            'raw_r_states':frame.raw_vote_average_state.value_counts().to_dict(),'raw_v_states':frame.raw_vote_count_state.value_counts().to_dict(),
            'raw_adult_states':frame.raw_adult_state.value_counts().to_dict(),'raw_video_states':frame.raw_video_state.value_counts().to_dict(),
            'rows_with_timestamp_field':int(frame.raw_timestamp_fields.ne('{}').sum()),'timestamp_interpretation':'tar mtime is archive metadata, not verified observation time',
            'base_eligible':int(base.sum()),'quality_eligible':len(order),'main_quality':qinfo,
            'country_KR':int(frame.production_country_codes.map(lambda a:'KR' in a).sum()),'credits_present':int(frame.raw_credits_present.sum()),
            'metadata_support':{c:int(frame[c].map(len).gt(0).sum()) for c in ['director_ids','top5_cast_ids','collection_ids','production_company_ids','production_country_codes']},
            'runtime':{'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,'threadpools':threadpool_info()},
            'keyword_policy':'archive hash reverified; exact pinned v1 keyword parsing reused',
            'sources':{str(p):pin(p) for p in [rawpath,keywordpath,OLDOUT/'prepare/catalog.parquet',OLDOUT/'final/bundle.pkl',OLDOUT/'final/assignments.parquet',contexts,audit,OLDOUT/'recommend/roles.json',original_path,role_path,OLDOUT/'recommend-seal.json']}}
    write(dest/'report.json',report);seal('prepare');print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    reviewed('prepare',['dv2_prepare.py'])
    with Guard('prepare'),threadpool_limits(limits=CFG['threads']):prepare()
