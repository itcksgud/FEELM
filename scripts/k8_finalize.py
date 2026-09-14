"""Freeze selected bundle, verify full-catalog invariants and make review packets."""
from collections import Counter
import pickle
import platform
import numpy as np
import pandas as pd
import sklearn
import scipy
from threadpoolctl import threadpool_limits
from k8_common import *

def finalize(cfg):
    verify_seal('prepare');verify_seal('train');verify_seal('recommend')
    dest=OUT/'final';dest.mkdir(parents=True,exist_ok=True)
    assert not (dest/'manifest.json').exists(),'preserve final artifact'
    frame=pd.read_parquet(OUT/'prepare/catalog.parquet');selection=read(OUT/'recommend/selection.json')
    with open(OUT/'train/hierarchies.pkl','rb') as f:bundle=pickle.load(f)[selection['selected_hierarchy']]
    before=pickle.dumps(bundle,protocol=5)
    result=assign(frame,bundle)
    for name in ['taste_id','child_id','group_id']:
        assert len(result[name])==cfg['expected_movies'] and (result[name]>=0).all()
    assert set(result['taste_id'])==set(range(8))
    np.testing.assert_array_equal(result['taste_id'],np.load(OUT/'train/top-labels.npy'))
    np.testing.assert_array_equal(result['group_id'],np.load(OUT/'train'/(selection['selected_hierarchy']+'-groups.npy')))
    baseline=result['taste_id']*1000+result['child_id']
    def check(a,expected):
        b=assign(a,bundle);np.testing.assert_array_equal(b['taste_id']*1000+b['child_id'],expected)
    # Full-row reordering and deletion; entire dataset in37 unequal batches.
    check(frame.iloc[::-1],baseline[::-1])
    keep=np.arange(len(frame))%7!=0;check(frame.loc[keep],baseline[keep])
    for ix in np.array_split(np.arange(len(frame)),37):check(frame.iloc[ix],baseline[ix])
    new=pd.DataFrame([{'service_movie_id':-1,'genre_ids':[],'keyword_ids':[],'overview':''},
                      {'service_movie_id':-2,'genre_ids':[9999999],'keyword_ids':[9999999],'overview':'zzzzzzoutofvocabulary'}])
    full_append=assign(pd.concat([frame,new],ignore_index=True),bundle)
    np.testing.assert_array_equal(full_append['taste_id'][:len(frame)]*1000+full_append['child_id'][:len(frame)],baseline)
    appended=assign(pd.concat([frame.iloc[:1000],new],ignore_index=True),bundle)
    np.testing.assert_array_equal(appended['taste_id'][:1000]*1000+appended['child_id'][:1000],baseline[:1000])
    assert not appended['content_supported'][-2:].any()
    with open(dest/'bundle-initial.pkl','wb') as f:pickle.dump(bundle,f,protocol=5)
    with open(dest/'bundle-initial.pkl','rb') as f:reloaded=pickle.load(f)
    rr=assign(frame,reloaded)
    np.testing.assert_array_equal(rr['taste_id']*1000+rr['child_id'],baseline)
    assert before==pickle.dumps(bundle,protocol=5),'assignment mutated frozen model'
    names=read(OUT/'prepare/names.json');genre_names=names['genres'];keyword_names=names['keywords']
    rows=frame[['service_movie_id','tmdb_id','movielens_movie_id','mapping_status','title','original_title','original_language','genre_present','keyword_present','overview_present','keyword_file_present','no_content','adult','video']].copy()
    for name,value in result.items():rows[name]=value
    rows['weak_evidence']=~rows.top_supported | (~rows.keyword_supported&~rows.overview_supported)
    rows['keyword_oov_only']=rows.keyword_present&~rows.keyword_supported
    rows['overview_oov_only']=rows.overview_present&~rows.overview_supported
    descriptors=[];top_names={};group_names={}
    def describe(mask,kind,key,parent=None):
        a=frame.loc[mask];genre=Counter(str(int(v)) for row in a.genre_ids for v in row);kw=Counter(str(int(v)) for row in a.keyword_ids for v in row)
        terms=[genre_names.get(k,k) for k,_ in genre.most_common(3)]
        keywords=[keyword_names.get(k,k) for k,_ in kw.most_common(8)]
        # Popularity used only for explanatory example selection after scores froze.
        popular=a.sort_values(['vote_count','service_movie_id'],ascending=[False,True]).head(8)
        korean=a[a.original_language.eq('ko')].sort_values(['vote_count','service_movie_id'],ascending=[False,True]).head(5)
        packet={'kind':kind,'id':int(key),'parent':parent,'count':len(a),'genre_top':[[genre_names.get(k,k),n] for k,n in genre.most_common(8)],
                'keyword_top':[[keyword_names.get(k,k),n] for k,n in kw.most_common(12)],
                'languages':a.original_language.value_counts().head(5).to_dict(),
                'no_content':int(a.no_content.sum()),'no_plot_or_keyword':int((~a.overview_present&~a.keyword_present).sum()),
                'top_unsupported':int((~rows.loc[mask,'top_supported']).sum()),
                'examples':popular[['service_movie_id','tmdb_id','title','original_title','vote_count']].to_dict(orient='records'),
                'korean_examples':korean[['service_movie_id','tmdb_id','title','original_title','vote_count']].to_dict(orient='records')}
        descriptors.append(packet)
        label=' · '.join(terms) if terms else '콘텐츠 근거 부족'
        return label
    for j in range(8):top_names[str(j)]=describe(result['taste_id']==j,'taste',j)
    for g in sorted(set(result['group_id'])):
        mask=result['group_id']==g;parent=int(result['taste_id'][mask][0])
        group_names[str(int(g))]=describe(mask,'group',g,parent)
    # Human display-name overrides are presentation-only and must be separately pinned.
    override=DOC/'display-names.json'
    if override.exists():
        names_override=read(override)
        for field,allowed in [('tastes',top_names),('groups',group_names)]:
            assert set(names_override.get(field,{}))<=set(allowed),'unknown display ID'
            assert all(isinstance(v,str) and v.strip() for v in names_override.get(field,{}).values()),'empty display name'
        top_names.update(names_override.get('tastes',{}));group_names.update(names_override.get('groups',{}))
    bundle['top_names']=top_names;bundle['group_names']=group_names
    rows['taste_name']=[top_names[str(int(j))] for j in rows.taste_id]
    rows['group_name']=[group_names[str(int(j))] for j in rows.group_id]
    rows['model_version']=bundle['version']
    with open(dest/'bundle.pkl','wb') as f:pickle.dump(bundle,f,protocol=5)
    rows.to_parquet(dest/'assignments.parquet',index=False);rows.to_csv(dest/'assignments.csv.gz',index=False,compression='gzip')
    write(dest/'descriptors.json',descriptors)
    inv={'status':'PASS','movies':len(rows),'unique_service_ids':int(rows.service_movie_id.nunique()),'tastes':8,'groups':len(group_names),
         'tests':['full_reversal','delete_every_seventh','37_batch_partitions','append_empty_OOV_new_records','save_reload_all_rows','original_labels_match','nonmutating_assign','exactly_one_nested_assignment'],
         'top_unsupported':int((~rows.top_supported).sum()),'no_content':int(rows.no_content.sum()),'weak_evidence':int(rows.weak_evidence.sum()),
         'keyword_oov_only':int(rows.keyword_oov_only.sum()),'overview_oov_only':int(rows.overview_oov_only.sum())}
    write(dest/'invariance.json',inv)
    manifest={'version':cfg['version'],'bundle':pin(dest/'bundle.pkl'),'inference_code':pin(ROOT/'scripts/k8_common.py'),
              'runtime':{'numpy':np.__version__,'sklearn':sklearn.__version__,'scipy':scipy.__version__,'python':platform.python_version()},
              'selection':selection,'fingerprint':fingerprint(),'input_catalog':pin(OUT/'prepare/catalog.parquet'),
              'names':{'tastes':top_names,'groups':group_names},'assignment_artifacts':{p.name:pin(p) for p in [dest/'assignments.parquet',dest/'assignments.csv.gz']}}
    write(dest/'manifest.json',manifest)
    seal('final');print(json.dumps(inv),flush=True)

if __name__=='__main__':
    cfg=read(DOC/'config.json');reviewed('final')
    with Guard(cfg,'final'),threadpool_limits(limits=cfg['threads']):finalize(cfg)
