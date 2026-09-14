"""Read-only factual audit of report examples against frozen local sources."""
from pathlib import Path
from collections import Counter
import csv,gzip,hashlib,json,pickle,re,sys
import numpy as np
import pandas as pd

OWN=Path(__file__).resolve().parents[1]
MAIN=Path('C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913')
BASE=MAIN/'outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2'
DOC=OWN/'docs/recommendation/experiments/fixed-k8-discovery-v2'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def pin(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def scalar_equal(a,b):
    if pd.isna(b):assert a is None
    elif isinstance(b,(float,np.floating)):assert np.isclose(a,b,rtol=0,atol=1e-12)
    else:assert a==b
def run():
    files=[BASE/'report/user-examples.json',BASE/'report/korean-familiar-movies.json']
    initial={p.name:pin(p) for p in files};examples,familiar=map(read,files)
    frame=pd.read_parquet(BASE/'prepare/catalog.parquet');q=np.load(BASE/'prepare/quality.npy');x=np.load(BASE/'prepare/content.npy',mmap_mode='r')
    assignments=pd.read_parquet(BASE/'final/assignments.parquet')
    cfg=read(MAIN/'docs/recommendation/experiments/fixed-k8-discovery-v2/config.json')
    namespath=Path(cfg['previous_root'])/'outputs/fixed-k8-discovery/prepare/names.json';names=read(namespath)
    assert pin(namespath)==read(BASE/'final/manifest.json')['label_source']
    hs={}
    for key in ['v1-fixed16','GKT-K128','GKT-K256']:
        with (BASE/'cluster'/f'{key}-hierarchy.pkl').open('rb') as f:hs[key]=pickle.load(f)
    raw,redactions=re.subn(rb'"ratings"\s*:\s*\[[^\]]*\]',b'"ratings":null',(BASE/'prepare/contexts.json').read_bytes());assert redactions==1350
    contexts={c['uid']:c for c in json.loads(raw) if c['cap']==10}
    requests=pd.read_parquet(BASE/'check/requests.parquet');roles=read(BASE/'prepare/roles.json')
    seen_movie_rows=[];groupstats={};low_star_connections=[];details_cache={};userfacts=[]
    def inspect_movie(m,hierarchy):
        i=m['index'];s=frame.iloc[i];a=assignments.iloc[i]
        assert int(s.service_movie_id)==m['service_movie_id'] and int(s.tmdb_id)==m['tmdb_id']
        scalar_equal(m['source_title'],s.title);scalar_equal(m['overview'],s.overview)
        for target,source in [('R','raw_vote_average_number'),('v','raw_vote_count_number'),('language','original_language'),('release_year','release_year')]:scalar_equal(m[target],s[source])
        scalar_equal(m['Q'],q[i]);assert m['production_KR']==('KR' in s.production_country_codes)
        assert m['tmdb_link']==f'https://www.themoviedb.org/movie/{int(s.tmdb_id)}'
        assert m['genres']==[names['genres'].get(str(int(k)),str(k)) for k in s.genre_ids]
        assert m['keywords']==[names['keywords'].get(str(int(k)),str(k)) for k in s.keyword_ids]
        assert m['taste_id']==int(s.taste_id) and m['taste_name']==a.taste_name
        gid=int(hs[hierarchy]['groups'][i]);assert m['group_id']==gid
        key=(hierarchy,gid)
        if key not in groupstats:
            members=np.flatnonzero(hs[hierarchy]['groups']==gid);rows=frame.iloc[members]
            counts=Counter(int(k) for ar in rows.genre_ids for k in ar)
            groupstats[key]={'hierarchy':hierarchy,'group_id':gid,'members':len(members),'genre_missing':sum(len(ar)==0 for ar in rows.genre_ids),'genre_counts':[(names['genres'].get(str(k),str(k)),v) for k,v in counts.most_common(5)],'supported_gkt_rows':int(np.count_nonzero(np.sum(x[members]*x[members],axis=1)>1e-12))}
        seen_movie_rows.append((i,m['service_movie_id'],hierarchy,gid))
    for m in familiar:
        inspect_movie(m,'v1-fixed16')
        for k in [128,256]:assert m[f'K{k}_group_id']==int(hs[f'GKT-K{k}']['groups'][m['index']])
    with (OWN/'outputs/dv2-korean-metadata-audit-r2/korean33-assignments.csv').open(encoding='utf-8-sig',newline='') as f:blind=list(csv.DictReader(f))
    assert {int(m['service_movie_id']) for m in familiar}=={int(m['service_movie_id']) for m in blind}
    for u in examples:
        uid=u['uid'];assert uid in roles['verification'];c=contexts[uid];history={m['service_movie_id']:m for m in u['history']}
        assert [(m['index'],m['stars']) for m in u['history']]==list(zip(c['history'],c['stars']))
        for m in u['history']:inspect_movie(m,'v1-fixed16')
        original=np.asarray(c['original_stars']);anchor=3+.5*((original.sum()+17.5)/(len(original)+5)-3)
        support=np.sum(x[c['history']]*x[c['history']],axis=1)>1e-12
        weighted=np.asarray(c['stars'])[support]-anchor
        rawprofile=np.sum(x[np.asarray(c['history'])[support]]*weighted[:,None],axis=0)/(sum(abs(weighted))+5)
        direction=rawprofile/np.linalg.norm(rawprofile)
        assert abs(anchor-u['profile']['anchor'])<1e-12
        with gzip.open(BASE/f'check/details/{uid}.json.gz','rt',encoding='utf-8') as f:dd={d['policy']:d for d in json.load(f)}
        controls=[{'policy':u['policy'],'recommendations':u['recommendations']}]+u['hierarchy_comparison']
        cf=[]
        for con in controls:
            match=requests[requests.uid.eq(uid)&requests.policy.eq(con['policy'])];assert len(match)==1;r=match.iloc[0];detail=dd[con['policy']]
            assert [m['index'] for m in con['recommendations']]==r.ranked.tolist()
            assert len(r.ranked)==10
            for j,m in enumerate(con['recommendations']):
                inspect_movie(m,r.hierarchy);i=m['index'];gid=m['group_id'];assert m['rank']==j+1
                assert i not in c['viewed'] and i in detail['candidate_indices']
                assert m['prediction']==r.ranked_prediction[j] and m['ml_train_count']==r.ranked_train_count[j]
                assert m['original_candidate_position']==r.ranked_candidate_position[j] and m['global_Q_rank_in_candidates']==r.ranked_quality_position[j]
                assert m['all_mapped_viewed']==len(c['viewed'])
                assert m['viewed_in_group']==sum(int(hs[r.hierarchy]['groups'][vi])==gid for vi in c['viewed'])
                assert m['viewed_in_group']<=2 and m['viewed_in_group']/max(1,len(c['viewed']))<=.2
                assert abs(m['content_similarity']-float(x[i]@direction))<1e-12
                hits=[]
                for hi in c['history']:
                    common=set(map(int,frame.keyword_ids.iloc[i]))&set(map(int,frame.keyword_ids.iloc[hi]))
                    hits.append({'history_service_id':int(frame.service_movie_id.iloc[hi]),'content_dot':float(x[i]@x[hi]),'shared_keywords':[names['keywords'].get(str(k),str(k)) for k in sorted(common)]})
                expected=sorted(hits,key=lambda z:-z['content_dot'])[:2];assert m['history_connections']==expected
                for connection in expected:
                    past=history[connection['history_service_id']]
                    if past['stars']<anchor:low_star_connections.append({'uid':uid,'policy':con['policy'],'recommendation':m['title'],'history':past['title'],'history_stars':past['stars'],'anchor':anchor,'content_dot':connection['content_dot'],'shared_keywords':connection['shared_keywords']})
            if con['policy']!=u['policy']:
                assert con['candidate_overlap_vs_exemplar']==len(set(detail['candidate_indices'])&set(dd[u['policy']]['candidate_indices']))/len(dd[u['policy']]['candidate_indices'])
                assert con['top10_overlap_vs_exemplar']==len(set(r.ranked)&set(m['index'] for m in u['recommendations']))/len(u['recommendations'])
            cf.append({'policy':con['policy'],'returned':len(con['recommendations']),'train_count_zero':sum(m['ml_train_count']==0 for m in con['recommendations']),'train_count_le20':sum(m['ml_train_count']<=20 for m in con['recommendations']),'release_after_2022':sum(m['release_year'] is not None and m['release_year']>2022 for m in con['recommendations']),'genre_missing':sum(not m['genres'] for m in con['recommendations']),'overview_missing':sum(not m['overview'] for m in con['recommendations'])})
        userfacts.append({'uid':uid,'history_count':len(u['history']),'anchor':anchor,'negative_history_stars':[(m['title'],m['stars']) for m in u['history'] if m['stars']<anchor],'policy_summaries':cf})
    assert {p.name:pin(p) for p in files}==initial
    assert not any(n.startswith(('dv2_predictor','combination340_models','final344_adapter')) for n in sys.modules)
    result={'status':'PASS','scope':'Factual exported metadata/history/rank/source-row and descriptive connection audit; no new model or target-quality evaluation','code':pin(Path(__file__)),'report_inputs':initial,'source_seals':{s:pin(BASE/(s+'-seal.json')) for s in ['prepare','cluster','check','final']},'name_source':pin(namespath),'users':len(examples),'familiar_movies':len(familiar),'movie_row_instances_verified':len(seen_movie_rows),'distinct_service_films_verified':len(set(i[1] for i in seen_movie_rows)),'recommendations_verified':sum(len(u['recommendations'])+sum(len(c['recommendations']) for c in u['hierarchy_comparison']) for u in examples),'user_facts':userfacts,'connections_to_below_anchor_history':low_star_connections,'group_descriptors':list(groupstats.values()),'future_numeric_rating_arrays_redacted_before_parse':redactions,'predictor_calls':0,'limits':['TMDB metadata are a snapshot, not independently verified real-world film facts.','history_connections are unsigned film-content similarities, not causal explanations of positive preference.','Current service catalog contains post-2022 films, so these examples are developmental and not historical 2023 availability reconstruction.','ML train count zero is a support fact, not proof of rating or recommendation quality.','Group genre labels omit counts; use group_descriptors denominators when interpreting them.','The audit uses prior full prepare source audits for raw-to-normalized catalog provenance.']}
    target=DOC/'user-examples-factual-review.json';assert not target.exists();target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','users':len(examples),'familiar':len(familiar),'rows':len(seen_movie_rows),'negative_connections':len(low_star_connections),'ledger':pin(target),'user_facts':userfacts},ensure_ascii=False,indent=2))
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');run()
