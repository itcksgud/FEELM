"""Descriptive, sealed-output reporting. Never selects or reruns a predictor."""
from collections import Counter
import gzip
import pickle
import numpy as np
import pandas as pd
from dv2_common import *

def finite(value):
    if isinstance(value,dict):return {str(k):finite(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,np.ndarray)):return [finite(v) for v in value]
    if isinstance(value,(np.integer,)):return int(value)
    if isinstance(value,(np.bool_,)):return bool(value)
    if isinstance(value,(float,np.floating)):return float(value) if np.isfinite(value) else None
    return value

def save(path,value):write(path,finite(value))
def avg(a):return float(np.mean(a)) if len(a) else None
def bins(v):
    if v==0:return '0'
    if v<=5:return '1-5'
    if v<=20:return '6-20'
    if v<=100:return '21-100'
    return '101+'
def era(y):
    if pd.isna(y):return 'missing'
    if y<=1999:return '<=1999'
    if y<=2009:return '2000-09'
    if y<=2019:return '2010-19'
    if y<=2022:return '2020-22'
    if y==2023:return '2023'
    if y<=2026:return '2024-26'
    return '>2026'
def language(l):return l if l in ['ko','en'] else ('missing' if pd.isna(l) or not l else 'other')

def summarize(a):
    v=a[a.profile_state.eq('VALID')];returned=v[v.returned.gt(0)]
    groups=read(OUT/'cluster/report.json')['hierarchies'][str(a.hierarchy.iloc[0])]['n_groups']
    return finite({'requests':len(a),'valid_users':len(v),'profile_states':dict(Counter(a.profile_state)),
      'returning_users':len(returned),'full10_share':float(v.returned.ge(10).mean()) if len(v) else None,
      'slots':int(v.returned.sum()),'requested_slots':10*len(v),'slot_supply':float(v.returned.sum()/(10*len(v))) if len(v) else None,
      'mean_candidates':float(v.candidate_count.mean()),'mean_model_calls':float(v.model_calls.mean()),
      'mean_als_rows':float(v.als_rows.mean()),'mean_gbt_rows':float(v.gbt_rows.mean()),
      'latency_p50_ms':float(v.latency_ms.quantile(.5)),'latency_p95_ms':float(v.latency_ms.quantile(.95)),
      'cpu_p95_ms':float(v.cpu_ms.quantile(.95)),'mean_content_comparisons':float(v.content_comparisons.mean()),
      'mean_representative_comparisons':float(v.representative_comparisons.mean()),
      'exhausted_users':int(v.retrieval_state.eq('ELIGIBLE_PREFIXES_EXHAUSTED').sum()),
      'no_eligible_users':int(v.retrieval_state.eq('NO_ELIGIBLE_DISCOVERY_GROUP').sum()),
      'hierarchy_groups':groups,'mean_legal_groups':float(v.legal_group_count.mean()),'mean_legal_group_share':float(v.legal_group_count.mean()/groups),
      'less_than_B_users':int(v.candidate_count.lt(v.budget).sum()),'mean_candidate_supply':float(v.candidate_supply.mean()),
      'reference_supply_defined_users':int(v.reference_supply.notna().sum()),'mean_reference_supply_defined':float(v.reference_supply.mean()),
      'tmdb_low20_top1':int(v.low_tmdb20_top1.sum()),'ml_low20_top1':int(v.low_ml20_top1.sum()),
      'tmdb_low20_top1_conditional':float(returned.low_tmdb20_top1.mean()),'ml_low20_top1_conditional':float(returned.low_ml20_top1.mean()),
      'q_order_tmdb_low20_top1':int(v.Q_order_low_tmdb20_top1.sum()),'q_order_ml_low20_top1':int(v.Q_order_low_ml20_top1.sum()),
      'mean_top1_quality_position':avg([int(r.ranked_quality_position[0]) for r in returned.itertuples()]),
      'observed_slots':int(v.observed_slots.sum()),'unknown_slots':int(v.unknown_slots.sum()),
      'unknown_slot_share':float(v.unknown_slots.sum()/v.returned.sum()) if v.returned.sum() else None,
      'strong_negative_slots':int(v.strong_negative_slots.sum()),'fixed8_unseen_slots':int(v.fixed8_unseen_slots.sum()),
      'seen_collection_slots':int(v.seen_collection_slots.sum()),'mean_genre_new_fraction':float(v.genre_new_fraction.mean()),
      'mean_keyword_seen_overlap':float(v.keyword_seen_overlap.mean()),
      'top8_slots':np.sum(list(v.top8_histogram),axis=0).tolist() if len(v) else [0]*8,
      'future_recall10_mean_defined':float(v.future_recall10.mean()),'future_recall_defined_users':int(v.future_recall10.notna().sum()),
      'future_ndcg10_mean_defined':float(v.future_ndcg10.mean())})

def strata(a,frame):
    out=[]
    for policy,allrows in a.groupby('policy'):
        rows=allrows[allrows.profile_state.eq('VALID')];slots=[]
        for r in rows.itertuples():
            for rank_,i in enumerate(r.ranked):
                f=frame.iloc[int(i)]
                slots.append({'uid':r.uid,'rank':rank_+1,'index':int(i),'tmdb':bins(r.ranked_tmdb_count[rank_]),
                  'ml':bins(r.ranked_train_count[rank_]),'language':language(f.original_language),
                  'production_KR':'KR' if 'KR' in f.production_country_codes else ('known_non_KR' if len(f.production_country_codes) else 'unknown'),'era':era(f.release_year)})
        sf=pd.DataFrame(slots)
        if sf.empty:continue
        returning=int(rows.returned.gt(0).sum())
        for field in ['tmdb','ml','language','production_KR','era']:
            for label,g in sf.groupby(field):
                top=g[g['rank'].eq(1)]
                out.append({'policy':policy,'dimension':field,'stratum':label,'top1':len(top),'slots':len(g),
                  'unique_films':int(g['index'].nunique()),'users':int(g.uid.nunique()),'all_valid_users':len(rows),
                  'returning_users':returning,'returned_slots':len(sf),'requested_slots':len(rows)*10,
                  'top1_conditional_share':len(top)/returning,'top1_all_valid_share':len(top)/len(rows),'slot_share':len(g)/len(sf)})
    return out

def paired(a,links):
    lookup={(r.uid,r.policy):r for r in a.itertuples()};out=[]
    for link in links:
        p=lookup.get((link['uid'],link['policy']));r=lookup.get((link['uid'],link['reference_policy']))
        if p is None or r is None or p.profile_state!='VALID' or r.profile_state!='VALID':continue
        ps=set(map(int,p.ranked));rs=set(map(int,r.ranked))
        if r.kind=='global_full_predictor':canonical=r.kind
        elif r.kind=='global_flat_q':canonical=f'{r.kind}:B{r.budget}'
        elif r.kind=='full_predictor':canonical=f'{r.kind}:{r.hierarchy}'
        else:canonical=r.policy
        out.append({'uid':p.uid,'policy':p.policy,'reference':canonical,'source_reference_policy':r.policy,'reference_kind':r.kind,'reused_cost_link':link['reference_reused_for_comparison'],
          'policy_returned':p.returned,'reference_returned':r.returned,'top10_overlap':len(ps&rs)/len(rs) if rs and ps else None,
          'top1_same':bool(p.ranked[0]==r.ranked[0]) if len(p.ranked) and len(r.ranked) else None,
          'mean_predicted_score_delta':avg(p.ranked_prediction)-avg(r.ranked_prediction) if ps and rs else None,
          'actual_predicted_movies':p.candidate_count,'reference_actual_predicted_movies':r.candidate_count,
          'latency_ms':p.latency_ms,'reference_latency_ms':r.latency_ms,
          'fixed8_new_slot_delta':p.fixed8_unseen_slots-r.fixed8_unseen_slots,
          'collection_repeat_slot_delta':p.seen_collection_slots-r.seen_collection_slots})
    return out

def error_panels(errors):
    rows=[]
    if errors.empty:return rows
    e=errors.copy();e['train_stratum']=e.train_count.map(bins)
    for col in ['language','release_year','supported_als_inputs','input_count','production_KR']:
        if col not in e:e[col]='unavailable'
    e['language_stratum']=e.language.map(language);e['era']=e.release_year.map(lambda y:era(y) if y!='unavailable' else y)
    for variant,v in e.groupby('variant'):
        for dim in ['all','train_stratum','language_stratum','era','supported_als_inputs','input_count','production_KR']:
            groups=[('all',v)] if dim=='all' else v.groupby(dim,dropna=False)
            for label,a in groups:
                rows.append({'variant':variant,'dimension':dim,'stratum':str(label),'rows':len(a),'users':int(a.uid.nunique()),
                  'macro_mse':float(a.groupby('uid').squared_error.mean().mean()),'micro_mse':float(a.squared_error.mean()),
                  'macro_mae':float(a.groupby('uid').absolute_error.mean().mean()),'micro_mae':float(a.absolute_error.mean()),
                  'mean_actual':float(a.actual.mean()),'mean_clipped_prediction':float(a.clipped.mean()),
                  'out_of_rating_range_unclipped':int(((a.prediction<.5)|(a.prediction>5)).sum())})
    return rows

FAMILIAR={22139:'워낭소리',59738:'님아, 그 강을 건너지 마오',82246:'BLACKPINK: 세상을 밝혀라',863:'괴물',58434:'곡성',73139:'곤지암',72564:'기생충',4955:'엽기적인 그녀',140173:'7번방의 선물',59652:'국제시장',68859:'택시운전사',45504:'인터스텔라',539:'올드보이',5166:'살인의 추억',71666:'범죄도시',76119:'극한직업',76335:'엑시트',609:'나 홀로 집에',44851:'인사이드 아웃',38814:'겨울왕국',100:'센과 치히로의 행방불명',12515:'인셉션',42012:'늑대소년',5332:'태극기 휘날리며'}
FAMILIAR.update({65874:'부산행',65942:'신과함께-죄와 벌',74127:'신과함께-인과 연',88879:'괴물(고레에다)',29791:'인사이드 아웃(동명 액션)',74333:'굿 보이즈',4835:'극장판 포켓몬스터: 결정탑의 제왕 엔테이'})
DISPLAY_FILMS={**FAMILIAR,111440:'Return',128570:'Harry'}

def examples(a,frame,dest,decision):
    assert pin(OLDOUT/'prepare/names.json')==read(OUT/'final/manifest.json')['label_source']
    names=read(OLDOUT/'prepare/names.json');contexts=read(OUT/'prepare/contexts.json');cmap={c['uid']:c for c in contexts if c['cap']==10}
    with (OUT/'final/bundle.pkl').open('rb') as f:bundle=pickle.load(f)
    assignments=pd.read_parquet(OUT/'final/assignments.parquet');q=np.load(OUT/'prepare/quality.npy');x=np.load(OUT/'prepare/content.npy')
    hierarchies={}
    for name in read(OUT/'cluster/report.json')['hierarchies']:
        with (OUT/f'cluster/{name}-hierarchy.pkl').open('rb') as f:hierarchies[name]=pickle.load(f)
    group_labels={}
    def movie(i,hierarchy=None):
        m=frame.iloc[int(i)];g=assignments.iloc[int(i)]
        group=int(g.group_id);label=g.group_name
        if hierarchy is not None:
            group=int(hierarchies[hierarchy]['groups'][int(i)]);key=(hierarchy,group)
            if key not in group_labels:
                counts=Counter(int(k) for ar in frame.genre_ids.iloc[np.flatnonzero(hierarchies[hierarchy]['groups']==group)] for k in ar)
                group_labels[key]=' / '.join(names['genres'].get(str(k),str(k)) for k,v in counts.most_common(3)) or 'Insufficient genre evidence'
            label=group_labels[key]
        return finite({'index':int(i),'service_movie_id':int(m.service_movie_id),'tmdb_id':int(m.tmdb_id),
          'title':DISPLAY_FILMS.get(int(m.service_movie_id),m.title),'source_title':m.title,'taste_id':int(m.taste_id),'taste_name':g.taste_name,
          'example_category':'insufficient-evidence counterexample' if int(m.service_movie_id) in [111440,128570] else 'film metadata example; familiarity not measured',
          'group_id':group,'group_name':label,'R':m.raw_vote_average_number,'v':m.raw_vote_count_number,'Q':q[int(i)],
          'genres':[names['genres'].get(str(int(k)),str(k)) for k in m.genre_ids],
          'keywords':[names['keywords'].get(str(int(k)),str(k)) for k in m.keyword_ids],
          'language':m.original_language,'production_KR':'KR' in m.production_country_codes,'release_year':m.release_year,
          'tmdb_link':f'https://www.themoviedb.org/movie/{int(m.tmdb_id)}','overview':m.overview})
    familiar=[]
    for i in np.flatnonzero(frame.service_movie_id.isin(DISPLAY_FILMS)):
        m=movie(i)
        for k in [128,256]:
            # Hierarchy groups use a stable known in-memory key; assignments files are separately sealed.
            h=hierarchies[f'GKT-K{k}']
            m[f'K{k}_group_id']=int(h['groups'][i])
        familiar.append(m)
    save(dest/'korean-familiar-movies.json',familiar)
    exemplar=decision['candidate'] or decision['best_approximation'];rows=a[a.policy.eq(exemplar)&a.profile_state.eq('VALID')&a.returned.gt(0)]
    candidate_users=[]
    for r in rows.itertuples():
        c=cmap[r.uid];hit=sum(int(frame.service_movie_id.iloc[i]) in FAMILIAR for i in c['history'])
        candidate_users.append((hit,r.uid))
    chosen=[uid for hit,uid in sorted(candidate_users,key=lambda t:(-t[0],t[1]))[:3]]
    comparable=rows[rows.comparable]
    if len(comparable):chosen.append(int(comparable.sort_values('absolute_gap',ascending=False).iloc[0].uid))
    cases=[]
    for uid in dict.fromkeys(chosen):
        row=rows[rows.uid.eq(uid)].iloc[0];c=cmap[uid];p,prof=profile(x,c)
        with gzip.open(OUT/f'check/details/{uid}.json.gz','rt',encoding='utf-8') as f:details={d['policy']:d for d in json.load(f)}
        detail=details[exemplar]
        history=[{**movie(i),'stars':float(r)} for i,r in zip(c['history'],c['stars'])]
        def recommendations(rr,dd):
            rec=[]
            for j,i in enumerate(rr.ranked):
                m=movie(i,rr.hierarchy);hits=[]
                for hi in c['history']:
                    kg=set(map(int,frame.keyword_ids.iloc[int(i)]))&set(map(int,frame.keyword_ids.iloc[hi]))
                    hits.append({'history_service_id':int(frame.service_movie_id.iloc[hi]),'content_dot':float(x[int(i)]@x[hi]),
                      'shared_keywords':[names['keywords'].get(str(k),str(k)) for k in sorted(kg)]})
                g=m['group_id'];m.update(rank=j+1,prediction=float(rr.ranked_prediction[j]),ml_train_count=int(rr.ranked_train_count[j]),
                  original_candidate_position=int(rr.ranked_candidate_position[j]),global_Q_rank_in_candidates=int(rr.ranked_quality_position[j]),
                  viewed_in_group=int(dd['retrieval']['viewed_counts'][g]),all_mapped_viewed=len(c['viewed']),
                  content_similarity=float(x[int(i)]@p),history_connections=sorted(hits,key=lambda h:-h['content_dot'])[:2])
                rec.append(m)
            return rec
        rec=recommendations(row,detail);others=[]
        for hierarchy in ['GKT-K128','GKT-K256']:
            name=decision['per_hierarchy'][hierarchy];rr=a[a.uid.eq(uid)&a.policy.eq(name)].iloc[0];dd=details[name]
            others.append({'policy':name,'legal_groups':int(rr.legal_group_count),'total_groups':hierarchies[hierarchy]['n_groups'],
              'candidate_count':int(rr.candidate_count),'recommendations':recommendations(rr,dd),
              'candidate_overlap_vs_exemplar':len(set(dd['candidate_indices'])&set(detail['candidate_indices']))/len(detail['candidate_indices']),
              'top10_overlap_vs_exemplar':len(set(map(int,rr.ranked))&set(map(int,row.ranked)))/len(row.ranked)})
        cases.append({'uid':uid,'selection':'fixed familiar-history ordering, plus worst absolute approximation gap; illustrative, not representative satisfaction',
          'policy':exemplar,'profile':prof,'history':history,'recommendations':rec,'hierarchy_comparison':others,'absolute_gap':row.absolute_gap,
          'prefix_overlap':row.prefix_overlap,'legal_group_count':row.legal_group_count,'candidate_count':row.candidate_count})
    save(dest/'user-examples.json',cases)

def plots(dest,decision,sensitivity):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':160})
    selected=set(decision['per_hierarchy'].values())
    rows=[r for r in read(OUT/'check/summary.json') if r['policy'] in selected]
    ordered=['v1-fixed16','GKT-K2','GKT-K8','GKT-K32','GKT-K64','GKT-K128','GKT-K256']
    rows.sort(key=lambda r:ordered.index(r['hierarchy']))
    labels=[r['hierarchy'].replace('GKT-','')+f"\nB{r['budget']}/Q{r['quota']}" for r in rows];xx=np.arange(len(rows))
    numeric=lambda v:float('nan') if v is None else float(v)
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
    axes[0].plot(xx,[numeric(r['mean_prefix_overlap'])*100 for r in rows],marker='o',label='DIRECT-PREFIX overlap')
    axes[0].plot(xx,[r['full_return_share']*100 for r in rows],marker='s',label='10 returned / valid users')
    axes[0].axhline(60,color='#888888',ls=':',lw=1);axes[0].set(ylabel='Percent',ylim=(0,105),title='Frozen per-hierarchy finalists on check users')
    axes[0].legend(loc='lower right');axes[0].set_xticks(xx,labels,rotation=20)
    axes[1].plot(xx,[numeric(r['absolute_gap_p95']) for r in rows],marker='o',color='#ad4c2c',label='p95 absolute content-mean gap')
    axes[1].axhline(.1,color='#888888',ls=':',lw=1,label='Preregistered tolerance 0.10')
    axes[1].set(ylabel='Unit-vector similarity difference',title='Representative approximation, not satisfaction')
    axes[1].set_xticks(xx,labels,rotation=20);axes[1].legend()
    fig.savefig(dest/'hierarchy-tradeoffs.png');plt.close(fig)
    ss=[r for r in sensitivity if not r['sensitivity'].startswith('CAP')]
    lab=[r['sensitivity'].replace('Q-','').replace('movie_mean','movie').replace('vote_mean','vote').replace('MAIN_m300','MAIN movie m300') for r in ss]
    xx=np.arange(len(ss));fig,ax=plt.subplots(figsize=(12,4.8),layout='constrained')
    ax.bar(xx-.18,[100*numeric(r['tmdb_low20_top1_conditional']) for r in ss],.36,label='TMDb votes <=20')
    ax.bar(xx+.18,[100*numeric(r['ml_low20_top1_conditional']) for r in ss],.36,label='MovieLens TRAIN ratings <=20')
    ax.set_xticks(xx,lab,rotation=35,ha='right');ax.set(ylabel='Top1 share among users receiving a result (%)',ylim=(0,105),title='Count signals stay separate; same frozen personal predictor')
    ax.legend();fig.savefig(dest/'quality-sensitivity.png');plt.close(fig)
    c=read(OUT/'prepare/quality-config.json')['C'];v=np.geomspace(1,20000,250)
    fig,axes=plt.subplots(1,2,figsize=(11,4),layout='constrained')
    for ax,R in zip(axes,[8.,5.]):
        for m in [100,300,1000]:ax.plot(v,(v*R+m*c)/(v+m),label=f'm={m}')
        ax.axhline(c,color='#777777',ls=':',label=f'Frozen C={c:.3f}')
        ax.set(xscale='log',xlabel='TMDb vote count',ylabel='Q',title=f'Fixed raw average R={R:g}');ax.legend()
    fig.savefig(dest/'quality-shrink.png');plt.close(fig)

def main():
    for stage in ['prepare','cluster','predictor','sweep','check','final']:verify(stage)
    dest=require_fresh('report','manifest.json')
    if not (OUT/'check/requests.parquet').exists():
        assert read(OUT/'check/report.json')['status']=='NO_COMPARABLE_POLICY_HOLD'
        for name in ['request-panels','sensitivity-panels','paired-reference-summary','prefix-drift-summary','user-examples']:
            save(dest/(name+'.json'),[])
        for name in ['support-language-era','predictor-check-strata','policy-summary']:
            pd.DataFrame(columns=['status']).to_csv(dest/(name+'.csv'),index=False)
        save(dest/'manifest.json',{'status':'NO_COMPARABLE_DATA','plots':'Not produced: no comparable policy selected',
          'human_discovery_value':'UNMEASURED','no_new_predictions':True,'no_reselection':True,'code':pin(Path(__file__)),
          'source_seals':{stage:pin(OUT/(stage+'-seal.json')) for stage in ['prepare','cluster','predictor','sweep','check','final']}})
        seal('report');return
    frame=pd.read_parquet(OUT/'prepare/catalog.parquet')
    a=pd.read_parquet(OUT/'check/requests.parquet');s=pd.read_parquet(OUT/'check/sensitivity-requests.parquet')
    decision=read(OUT/'sweep/decision.json');exemplar=decision['candidate'] or decision['best_approximation']
    panels=[{'policy':name,**summarize(g)} for name,g in a.groupby('policy')]
    sensitivity=[{'sensitivity':'MAIN_m300','policy':exemplar,**summarize(a[a.policy.eq(exemplar)])}]
    sensitivity.extend({'sensitivity':str(g.sensitivity.iloc[0]),'policy':name,**summarize(g)} for name,g in s.groupby('policy'))
    save(dest/'request-panels.json',panels);save(dest/'sensitivity-panels.json',sensitivity)
    same=pd.read_parquet(OUT/'predictor/same-candidate-requests.parquet');samepanels=[]
    for variant,v in same.groupby('predictor'):
        cohort_slots=sum(sum(bool(available) and int(count)<=20 for available,count in zip(r.ranked_als_available,r.ranked_train_count)) for r in v.itertuples())
        samepanels.append({'variant':variant,**summarize(v),'original_als_low20_top1':int(v.original_als_low20_top1.sum()),
          'original_als_low20_slots':int(cohort_slots),'all_ml_low20_slots':int(v.low_ml20_slots.sum()),
          'tmdb_low20_slots':int(v.low_tmdb20_slots.sum()),'cohort_definition':'same original ALS availability, TRAIN count<=20; independent of chosen branch'})
    save(dest/'same-candidate-predictor-panels.json',samepanels)
    pd.DataFrame(strata(pd.concat([a,s],ignore_index=True),frame)).to_csv(dest/'support-language-era.csv',index=False)
    pairs=pd.DataFrame(paired(a,read(OUT/'check/reference-links.json')));pairs.to_parquet(dest/'paired-references.parquet',index=False)
    aggregates=[]
    for (p,r),v in (pairs.groupby(['policy','reference']) if len(pairs) else []):
        assert not v.uid.duplicated().any(),'one canonical reference comparison per policy/user'
        aggregates.append({'policy':p,'reference':r,'reference_kind':v.reference_kind.iloc[0],'common_valid_users':len(v),
          'comparable_returned_users':int(v.top10_overlap.notna().sum()),'mean_top10_overlap':float(v.top10_overlap.mean()),
          'top1_same_share':float(v.top1_same.dropna().astype(float).mean()),'mean_predicted_score_delta':float(v.mean_predicted_score_delta.mean()),
          'mean_actual_predicted_movies':float(v.actual_predicted_movies.mean()),'mean_reference_predicted_movies':float(v.reference_actual_predicted_movies.mean()),
          'latency_p95_ms':float(v.latency_ms.quantile(.95)),'reference_latency_p95_ms':float(v.reference_latency_ms.quantile(.95)),
          'mean_fixed8_new_slot_delta':float(v.fixed8_new_slot_delta.mean()),'mean_collection_repeat_slot_delta':float(v.collection_repeat_slot_delta.mean())})
    save(dest/'paired-reference-summary.json',aggregates)
    errors=pd.read_parquet(OUT/'check/observed-errors.parquet');pd.DataFrame(error_panels(errors)).to_csv(dest/'predictor-check-strata.csv',index=False)
    drift=pd.read_parquet(OUT/'check/prefix-drift.parquet');driftrows=[]
    save(dest/'prefix-drift-profile-states.json',dict(Counter(drift.profile_state)))
    drift=drift[drift.profile_state.eq('VALID')]
    for policy_,g in drift.groupby('policy'):
        row={'policy':policy_,'user_groups':len(g),'users':int(g.uid.nunique()),'groups':int(g.group_id.nunique())}
        for col in ['whole_to_actual_distance','whole_to_fixed_distance']:
            row[col+'_mean']=float(g[col].mean());row[col+'_p95']=float(g[col].quantile(.95));row[col+'_max']=float(g[col].max())
        row['absolute_similarity_mismatch_mean']=float((g.actual_mean_similarity-g.whole_mean_similarity).abs().mean())
        row['worst']=g.loc[g.whole_to_actual_distance.idxmax()].to_dict();driftrows.append(row)
    save(dest/'prefix-drift-summary.json',driftrows)
    policyrows=[]
    for stage in ['sweep','check']:
        for r in read(OUT/stage/'summary.json'):
            rr={**r,'stage':stage,'failed_gates':','.join(k for k,v in r['gates'].items() if not v)};rr.pop('gates');policyrows.append(rr)
    pd.DataFrame(policyrows).to_csv(dest/'policy-summary.csv',index=False)
    if (OUT/'final/bundle.pkl').exists():examples(a,frame,dest,decision)
    plots(dest,decision,sensitivity)
    resources={p.stem:read(p) for p in sorted(OUT.glob('*-resources.json'))}
    save(dest/'resource-summary.json',resources)
    save(dest/'manifest.json',{'status':'DESCRIPTIVE_REPORT_GENERATED_REVIEW_PENDING','human_discovery_value':'UNMEASURED',
      'code':pin(Path(__file__)),'source_seals':{stage:pin(OUT/(stage+'-seal.json')) for stage in ['prepare','cluster','predictor','sweep','check','final']},
      'no_new_predictions':True,'no_reselection':True,'exemplar':exemplar})
    seal('report');print('REPORT_COMPLETE',exemplar,flush=True)

if __name__=='__main__':
    reviewed('report',['dv2_report.py'])
    with Guard('report'):main()
