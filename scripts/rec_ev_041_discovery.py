"""Fixed 2x2 discovery diagnostic. Seal selections before decoding derived E/H."""
from collections import Counter
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
from rec_ev_033_tastes import Budget, pin, read_json, require, sha, write_json
from rec_ev_040_set_rule import Run as PreviousRun
from rec_ev_034_supply import inverted_features
from rec_ev_034_gating_repair import support_masks
from feelm_policy_states import relative_weights
from feelm_discovery_policies import POLICIES, select_policy, quality_bounds, opportunity

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'docs/recommendation/experiments/rec-ev-041'
FILES={'design':PLAN/'README.md','config':PLAN/'config.json','runner':Path(__file__).resolve(),
       'helper':ROOT/'scripts/feelm_discovery_policies.py','tests':ROOT/'scripts/tests/test_feelm_discovery_policies.py'}
for name in ('rec_ev_033_tastes.py','rec_ev_034_supply.py','rec_ev_034_gating_repair.py','feelm_policy_states.py',
             'rec_ev_040_set_rule.py','rec_ev_039_candidate_contract.py','rec_ev_038_clusters.py',
             'feelm_genre_candidates.py','feelm_genre_set_rule.py'):
    FILES[name]=ROOT/'scripts'/name
METHODS=('RULE_GENRE_SET','KM_GENRE')
SCORE_FILES={'eligibility.npz','typed100.npz','user-supply.parquet','slots.parquet','supply-summary.json'}
EVAL_FILES={'opportunities.parquet','quality.parquet','evaluation-summary.json'}
OUTPUTS=SCORE_FILES|EVAL_FILES|{'role-seal.json','score-seal.json','budget.json','master-before/index.json'}


def fingerprint(): return {k:sha(p) for k,p in FILES.items()}


def absence_reason(counts, suffix, final_stage='TOP100'):
    for stage,reason in [('CATALOG','NO_POLICY_ELIGIBLE'),('ALS_SUPPORTED','NO_ALS_FACTOR'),
                         ('RAW500','LOST_AT_500'),('TOP100','LOST_AT_100')]:
        if counts[stage]['D_'+suffix]==0:return reason
        if stage==final_stage:break
    return 'D_AVAILABLE'


class Run:
    def __init__(self):
        self.identity=fingerprint();self.cfg=read_json(FILES['config'])
        review=read_json(PLAN/'review.json')
        require(review['status']=='PASS' and review['fingerprint']==self.identity,'review missing/stale')
        self.out=ROOT/self.cfg['output_root']
        require(self.out.resolve()==ROOT/'outputs/recommendation-evidence/rec-ev-041','unexpected output root')
        self.paths={k:ROOT/v['path'] for k,v in self.cfg['inputs'].items()}
        self.previous=read_json(self.paths['review040'])
        self.master={p:v for p,v in self.previous['final_report_pins'].items()
                     if p.startswith('docs/recommendation/plans/service-policy-redesign/') or p=='docs/runbook/local-development.md'}
        require(len(self.master)==5 and self.cfg['n']==30 and self.cfg['users']==2180 and self.cfg['items']==85517
                and self.cfg['candidate_size']==500 and self.cfg['topn_size']==100,'setting drift')

    def sources(self,existing=False):
        require(fingerprint()==self.identity,'code changed')
        for key,spec in self.cfg['inputs'].items():
            require(pin(self.paths[key])=={k:spec[k] for k in ['bytes','sha256']},'input changed: '+key)
        prev=PreviousRun();prev.sources(existing=True)
        seal=read_json(self.paths['seal040'])
        require(self.previous['status']=='PASS_AUDIT_COMPLETE' and seal['fingerprint']==prev.identity
                and pin(self.paths['seal040'])==self.previous['completion_seal'],'REC040 lineage')
        for n,spec in seal['artifacts'].items():require(pin(self.paths['seal040'].parent/n)==spec,'REC040 output changed')
        for path,spec in self.previous['final_report_pins'].items():
            p=self.out/'master-before'/path if existing and path in self.master else ROOT/path
            require(pin(p)==spec,'historical report changed: '+path)
        audit034=read_json(self.paths['review034']);s034=read_json(self.paths['seal034'])
        require(audit034['status']=='PASS' and pin(self.paths['seal034'])==audit034['completion'],'raw500 lineage')
        auditE=read_json(self.paths['reviewE']);sE=read_json(self.paths['sealE'])
        require(auditE['status']=='PASS' and sha(self.paths['sealE'])==auditE['fingerprints']['completion_seal'],'E lineage')
        for base,s in [(self.paths['seal034'].parent,s034),(self.paths['sealE'].parent,sE)]:
            require(s['status']=='COMPLETE','predecessor incomplete')
            for n,spec in s['outputs'].items():require(pin(base/n)==spec,'predecessor output changed: '+n)
        prep=read_json(self.paths['prepare_seal']);train=read_json(self.paths['train_seal'])
        require(prep['status']==train['status']=='COMPLETE' and prep['overlap']==0 and prep['evaluation_users']==2180
                and train['training_users']==46376 and train['prepare_seal']==pin(self.paths['prepare_seal']),'ALS separation lineage')
        for k in ['profiles','prepared']:require(prep['outputs'][self.paths[k].name]==pin(self.paths[k]),'input preparation lineage')
        require(train['outputs']['factors.npz']==pin(self.paths['factors']),'factor identity')

    def snapshot(self):
        for p in self.master:
            target=self.out/'master-before'/p;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/p,target)
        write_json(self.out/'master-before/index.json',{'source_review':pin(self.paths['review040']),
                   'historical_master_pins':self.master,'current_master_may_advance':True})

    def score(self):
        write_json(self.out/'role-seal.json',{'status':'ROLES_FIXED','fingerprint':self.identity,'inputs':self.cfg['inputs'],
                   'n':30,'users':2180,'E_H_decoded':False,'E_H_allowed_only_after_score_seal':True,
                   'new_fit':False,'new_fold_in':False,'experience':'O30_PROXY_NOT_FULL_HISTORY'})
        with np.load(self.paths['prepared'],allow_pickle=False) as z:ids=z['item_ids'];prior=z['prior']
        require(len(ids)==85517 and (np.diff(ids)>0).all(),'catalog identity')
        # Factor support only; factors, counts and training_user_ids are not decoded.
        with np.load(self.paths['factors'],allow_pickle=False) as z:fids=z['item_ids']
        has=np.isin(ids,fids)
        profiles=pd.read_parquet(self.paths['profiles']).sort_values('user_key').reset_index(drop=True)
        keys=profiles.user_key.to_numpy(dtype=str)
        require(len(keys)==len(set(keys))==2180,'cohort identity')
        prefix=np.load(self.paths['raw500'],allow_pickle=False)
        require(np.array_equal(prefix['user_keys'],keys),'raw500 users differ')
        rawids,rawscores,lengths=prefix['movie_ids'],prefix['scores'],prefix['lengths']
        require(rawids.shape==rawscores.shape==(2180,500) and lengths.shape==(2180,),'raw500 shape')
        meta=pd.read_parquet(self.paths['metadata']).set_index('movie_id').loc[ids]
        structure=pd.read_parquet(self.paths['structured']).set_index('movie_id').loc[ids]
        format_only=np.array([set(gs)=={10770} for gs in meta.genre_ids])
        require(int(format_only.sum())==12,'format-only source drift')
        old=pd.read_parquet(self.paths['assignments039']);new=pd.read_parquet(self.paths['assignments040'])
        native=[]
        for method,frame in [(METHODS[0],new),(METHODS[1],old[old.method=='KM_GENRE'])]:
            part=frame.sort_values('movie_id')
            require(np.array_equal(part.movie_id.to_numpy(),ids),'mapping IDs differ')
            a=part.native_label.to_numpy(dtype=np.int64).copy()
            require(np.isin(a,np.arange(-1,8)).all(),'invalid native label')
            a[format_only]=-1;native.append(a)
        tokens,inverted=inverted_features([{'genres':[int(g) for g in row.genre_ids if g!=10770],
                'directors':row.director_ids,'keywords':row.keyword_ids} for row in structure.itertuples()])
        for i,row in enumerate(structure.itertuples()):
            require(sorted(row.genre_ids)==sorted(meta.iloc[i].genre_ids) and sorted(row.keyword_ids)==sorted(meta.iloc[i].keyword_ids),'content identity drift')
        packed=np.zeros((2180,2,2,(len(ids)+7)//8),dtype=np.uint8)
        top100=np.full((2180,2,100),-1,dtype=np.int64);top_lengths=np.zeros((2180,2),dtype=np.int64)
        supply=[];slots=[];allpos=np.arange(len(ids))
        for u,row in enumerate(profiles.itertuples(index=False)):
            observed_ids=np.asarray(row.profile_movie_ids,dtype=np.int64);indices=np.asarray(row.profile_rating_indices)
            require(len(observed_ids)==len(set(observed_ids.tolist()))==len(indices)==30
                    and np.isin(observed_ids,ids).all() and np.isin(indices,np.arange(10)).all(),'profile contract')
            obs=np.searchsorted(ids,observed_ids);excluded=np.zeros(len(ids),dtype=bool);excluded[obs]=True
            weights=np.asarray(relative_weights(((indices+1)/2).tolist(),prior),dtype=np.float64)
            length=int(lengths[u]);require(0<=length<=500,'raw500 length')
            mids=rawids[u,:length];scores=rawscores[u,:length]
            require(len(set(mids.tolist()))==length and np.isin(mids,ids).all() and np.isfinite(scores).all()
                    and (np.diff(scores)<=0).all(),'raw500 ordering')
            require(all(scores[i]!=scores[i+1] or mids[i]<mids[i+1] for i in range(max(0,length-1))),'raw500 tie ordering')
            raw=np.searchsorted(ids,mids);require(has[raw].all(),'raw500 factor unsupported')
            for mi,method in enumerate(METHODS):
                t,d,anchors,diag=support_masks(native[mi],obs,weights,tokens,inverted)
                require(not (t&d).any() and not t[format_only].any() and not d[format_only].any(),'role overlap/format leak')
                packed[u,mi]=np.packbits(np.stack([t,d]),axis=1)
                typed=raw[(t|d)[raw]][:100];top100[u,mi,:len(typed)]=typed;top_lengths[u,mi]=len(typed)
                stages={'CATALOG':allpos,'ALS_SUPPORTED':allpos[has],'RAW500':raw,'TOP100':typed}
                counts={}
                for stage,pool in stages.items():
                    avail=pool[~excluded[pool]]
                    counts[stage]={'T_before':int(t[pool].sum()),'D_before':int(d[pool].sum()),
                                   'T_after':int(t[avail].sum()),'D_after':int(d[avail].sum())}
                    supply.append({'user_key':keys[u],'method':method,'stage':stage,'pool_before':len(pool),'pool_after':len(avail),
                        **counts[stage],**diag,'unassigned_inputs':int((native[mi][obs]<0).sum()),
                        'experience_proxy_groups':int(len(set(native[mi][obs][native[mi][obs]>=0]))),'raw500_length':length})
                raw_lookup={int(p):float(s) for p,s in zip(raw,scores)}
                for stage in ['RAW500','TOP100']:
                    literal=('REMOVED_BY_EXCLUSION' if counts[stage]['D_before'] and not counts[stage]['D_after']
                             else absence_reason(counts,'before',stage))
                    aligned=absence_reason(counts,'after',stage)
                    pool=stages[stage];pool_scores=np.array([raw_lookup[int(p)] for p in pool])
                    results=[select_policy(pool,pool_scores,t,d,excluded,p) for p in POLICIES]
                    require(results[0]['taste2']==results[1]['taste2'],'policy changed T2')
                    if results[1]['status']=='T2_D1':require(results[1]['selected']==results[0]['selected'],'guard selected different D')
                    for result in results:
                        selected=result['selected'];facts=[]
                        for pos,kind in zip(selected,result['types']):
                            if kind=='DISCOVERY':
                                facts=[{'anchor_movie_id':int(ids[a]),'field':field,'id':int(tid)}
                                    for a in sorted(anchors,key=lambda p:ids[p]) for field,tid in sorted(tokens[pos]&tokens[a])]
                                require(bool(facts),'D without verified connection')
                        require(len(set(selected))==len(selected) and not excluded[selected].any(),'duplicate/excluded selection')
                        slots.append({'user_key':keys[u],'method':method,'stage':stage,**result,
                            'selected_movie_ids':[int(ids[p]) for p in selected],
                            'taste2_movie_ids':[int(ids[p]) for p in result['taste2']],
                            'selected_scores':[raw_lookup[p] for p in selected],
                            'reference_movie_id':int(ids[result['reference_position']]) if result['reference_position'] is not None else None,
                            'D_connection_facts':facts,'structural_D_loss_literal':literal,
                            'structural_D_loss_aligned':aligned,'ALS_active':length>0})
            if u%50==0:self.budget.guard()
        np.savez_compressed(self.out/'eligibility.npz',user_keys=keys,movie_ids=ids,methods=np.array(METHODS),
                            roles=np.array(['T','D']),packed=packed,native_labels=np.stack(native),factor_support=has,format_only=format_only)
        np.savez_compressed(self.out/'typed100.npz',user_keys=keys,methods=np.array(METHODS),positions=top100,lengths=top_lengths)
        sf=pd.DataFrame(slots);pd.DataFrame(supply).to_parquet(self.out/'user-supply.parquet',index=False)
        sf.to_parquet(self.out/'slots.parquet',index=False)
        rows=[]
        for (method,policy,stage),part in sf.groupby(['method','policy','stage']):
            require(len(part)==2180 and part.user_key.is_unique,'lost users')
            rows.append({'method':method,'policy':policy,'stage':stage,'users':2180,'status_counts':part.status.value_counts().to_dict(),
                         'valid_three':int(part.valid_three.sum()),'D_provided':int((part.status=='T2_D1').sum()),
                         'score_abstentions':int((part.status=='D_SCORE_GUARD_T3').sum()),
                         'reference_abstentions':int((part.status=='NO_T3_REFERENCE').sum()),
                         'D_loss_aligned':part.structural_D_loss_aligned.value_counts().to_dict(),
                         'D_loss_literal':part.structural_D_loss_literal.value_counts().to_dict()})
        reference_change=[]
        for method in METHODS:
            p=sf[(sf.method==method)&(sf.policy=='T3_GUARD')].pivot(index='user_key',columns='stage',values='reference_movie_id')
            r=p.RAW500;t=p.TOP100
            reference_change.append({'method':method,'raw500_reference_present':int(r.notna().sum()),
                'top100_reference_present':int(t.notna().sum()),'lost_at100':int((r.notna()&t.isna()).sum()),
                'changed_when_both':int((r.notna()&t.notna()&(r!=t)).sum())})
        write_json(self.out/'supply-summary.json',{'cells':rows,'reference_comparison':reference_change,
                   'users':2180,'n':30,'new_fits':0,'new_fold_in':0,'E_H_decoded':False,'actual_full_watch_history':False})
        write_json(self.out/'score-seal.json',{'status':'SCORES_AND_ELIGIBILITY_SEALED','fingerprint':self.identity,
                   'inputs':self.cfg['inputs'],'E_H_decoded':False,'outputs':{n:pin(self.out/n) for n in sorted(SCORE_FILES)}})

    def evaluate(self):
        seal=read_json(self.out/'score-seal.json')
        require(seal['status']=='SCORES_AND_ELIGIBILITY_SEALED' and seal['fingerprint']==self.identity
                and seal['E_H_decoded'] is False and set(seal['outputs'])==SCORE_FILES,'score phase not sealed')
        for n,spec in seal['outputs'].items():require(pin(self.out/n)==spec,'scored output changed before E')
        z=np.load(self.out/'eligibility.npz',allow_pickle=False);typed=np.load(self.out/'typed100.npz',allow_pickle=False)
        keys=z['user_keys'];ids=z['movie_ids'];packed=z['packed'];has=z['factor_support']
        require(np.array_equal(keys,typed['user_keys']) and np.array_equal(z['methods'],METHODS),'sealed axes')
        top_positions,top_lengths=typed['positions'],typed['lengths']
        profiles=pd.read_parquet(self.paths['profiles'],columns=['user_key','profile_movie_ids']).set_index('user_key')
        prefix=np.load(self.paths['raw500'],allow_pickle=False)
        sf=pd.read_parquet(self.out/'slots.parquet').set_index(['user_key','method','stage','policy'])
        # First E/H payload decoding occurs only after the score seal checks above.
        labels=pd.read_parquet(self.paths['labelsE'])
        h=np.load(self.paths['histogramsE'],allow_pickle=False)
        require(np.array_equal(h['user_keys'],keys) and h['histograms'].shape==(2180,10),'H identity')
        require(len(labels)==191933 and not labels.duplicated(['user_key','movie_id']).any() and set(labels.user_key)==set(keys),'E identity')
        byuser={k:p for k,p in labels.groupby('user_key')};quality=[];opportunities=[]
        for u,key in enumerate(keys):
            rows=byuser[key];mids=rows.movie_id.to_numpy(dtype=np.int64)
            require(np.isin(mids,ids).all() and not set(mids)&set(profiles.loc[key,'profile_movie_ids']),'E/O or catalog overlap')
            ratings=rows.rating_raw.to_numpy(dtype=float)
            require(np.isin(ratings,np.arange(.5,5.1,.5)).all(),'E half-star grid')
            hist=h['histograms'][u].astype(np.int64);require((hist>=0).all() and hist.sum()>0,'H invalid')
            indices=(ratings*2-1).astype(int);cumulative=np.concatenate([[0],np.cumsum(hist)])
            nums=2*cumulative[indices]+hist[indices];denom=2*int(hist.sum());qs=nums/denom
            require(np.allclose(qs,rows.q,atol=1e-12,rtol=0),'derived Q mismatch')
            known={int(mid):(float(q),float(raw)) for mid,q,raw in zip(mids,qs,ratings)}
            ep=np.searchsorted(ids,mids);excluded=np.zeros(len(ids),dtype=bool)
            excluded[np.searchsorted(ids,np.asarray(profiles.loc[key,'profile_movie_ids'],dtype=int))]=True
            length=int(prefix['lengths'][u]);raw=np.searchsorted(ids,prefix['movie_ids'][u,:length])
            score_lookup={int(p):float(s) for p,s in zip(raw,prefix['scores'][u,:length])}
            for mi,method in enumerate(METHODS):
                t,d=np.unpackbits(packed[u,mi],axis=1,count=len(ids)).astype(bool)
                stages={'CATALOG':np.flatnonzero(~excluded),'ALS_SUPPORTED':np.flatnonzero(has&~excluded),
                        'RAW500':raw[~excluded[raw]],
                        'TOP100':top_positions[u,mi,:top_lengths[u,mi]][~excluded[top_positions[u,mi,:top_lengths[u,mi]]]]}
                for stage,pool in stages.items():
                    inside=np.zeros(len(ids),dtype=bool);inside[pool]=True
                    for role,mask in [('T_STRUCTURAL',t),('D_STRUCTURAL',d)]:
                        eligible=inside&mask;ev=qs[eligible[ep]].tolist()
                        opportunities.append({'user_key':key,'method':method,'stage':stage,'role':role,
                            'policy':'SHARED','reference_defined':None,**opportunity(ev,int(eligible.sum()))})
                    if stage not in ('RAW500','TOP100'):continue
                    gr=sf.loc[(key,method,stage,'T3_GUARD')]
                    defined=not pd.isna(gr.reference_score)
                    guarded=np.zeros(len(ids),dtype=bool)
                    if defined:
                        candidates=pool[d[pool]]
                        for p in candidates:guarded[p]=score_lookup[int(p)]>=float(gr.reference_score)
                    guarded_opportunity=opportunity(qs[guarded[ep]].tolist(),int(guarded.sum()))
                    if not defined:guarded_opportunity['high_existence']='REFERENCE_UNDEFINED'
                    opportunities.append({'user_key':key,'method':method,'stage':stage,'role':'D_GUARDED',
                        'policy':'T3_GUARD','reference_defined':defined,**guarded_opportunity})
                    for policy in POLICIES:
                        row=sf.loc[(key,method,stage,policy)]
                        selected=list(map(int,row.selected_movie_ids));kinds=list(row.types)
                        ts=list(map(int,row.taste2_movie_ids))
                        ds=[mid for mid,kind in zip(selected,kinds) if kind=='DISCOVERY']
                        fallback=selected[2:3] if len(selected)==3 and kinds==['TASTE']*3 else []
                        for role,movies,required in [('T2',ts,2),('D1',ds,1),('FALLBACK_T3',fallback,1)]:
                            values=[known[mid][0] if mid in known else None for mid in movies]
                            raws=[known[mid][1] if mid in known else None for mid in movies]
                            role_mask=(guarded if policy=='T3_GUARD' else inside&d) if role=='D1' else inside&t
                            if role=='FALLBACK_T3':
                                role_mask=role_mask.copy();role_mask[np.searchsorted(ids,np.asarray(ts,dtype=np.int64))]=False
                            observed_high=int(((qs>=.8)&role_mask[ep]).sum())
                            selected_high=sum(v is not None and v>=.8 for v in values)
                            quality.append({'user_key':key,'method':method,'policy':policy,'stage':stage,'role':role,
                                'status':row.status,'valid_three':bool(row.valid_three),'movie_ids':movies,'q':values,'raw_ratings':raws,
                                'observed_high_in_role_pool':observed_high,
                                'observed_high_not_selected':observed_high-selected_high,
                                **quality_bounds(values,required)})
            if u%50==0:self.budget.guard()
        of=pd.DataFrame(opportunities);qf=pd.DataFrame(quality)
        of.to_parquet(self.out/'opportunities.parquet',index=False);qf.to_parquet(self.out/'quality.parquet',index=False)
        aggregates=[]
        for (method,policy,stage,role),part in qf.groupby(['method','policy','stage','role']):
            require(len(part)==2180 and part.user_key.is_unique,'quality denominator drift')
            avail=part[part.available];n=len(avail);known_count=int(avail.known.sum());unknown_count=int(avail.unknown.sum())
            raw_values=[float(v) for values in avail.raw_ratings for v in values if not pd.isna(v)]
            record={'method':method,'policy':policy,'stage':stage,'role':role,'requests':2180,'available_lists':n,
                'known_slots':known_count,'unknown_slots':unknown_count,'fully_labeled_lists':int((avail.unknown==0).sum()),
                'users_with_unselected_observed_high':int((part.observed_high_not_selected>0).sum()),
                'known_raw_histogram':dict(sorted(Counter(str(v) for v in raw_values).items()))}
            for metric in ['mean','min','harm']:
                record[metric+'_bounds']=[float(avail[metric+'_low'].mean()),float(avail[metric+'_high'].mean())] if n else None
            if role=='D1':
                record['harm_per_request_bounds']=[float(avail.harm_low.sum()/2180),float(avail.harm_high.sum()/2180)]
                record['known_only_harm_fraction']=float(avail[avail.known==1].harm_low.mean()) if known_count else None
                record['D_provision_fraction']=n/2180
            aggregates.append(record)
        opp=[]
        for (method,stage,role),part in of.groupby(['method','stage','role']):
            require(len(part)==2180 and part.user_key.is_unique,'opportunity denominator drift')
            opp.append({'method':method,'stage':stage,'role':role,'users':2180,
                'users_with_eligible':int((part.eligible>0).sum()),'users_with_known':int((part.known>0).sum()),
                'users_with_observed_low':int((part.known_low>0).sum()),'users_with_observed_high':int((part.known_high>0).sum()),
                'high_existence_counts':part.high_existence.value_counts().to_dict(),
                'eligible_pairs':int(part.eligible.sum()),'known_pairs':int(part.known.sum()),'unknown_pairs':int(part.unknown.sum()),
                'undefined_reference_users':int(part.reference_defined.eq(False).sum()) if role=='D_GUARDED' else None})
        write_json(self.out/'evaluation-summary.json',{'quality':aggregates,'opportunities':opp,'users':2180,'E_pairs':191933,
            'profile_E_overlap':0,'Q_H_revalidated':True,'new_fits':0,'new_fold_in':0,'conditional_reranking':False,
            'bounds_are_missing_label_identification_not_confidence_intervals':True,
            'actual_novelty_or_acceptability_measured':False,'policy_winner_selected':False,'K_switch_selected':False})

    def run(self):
        seal_path=self.out/'completion-seal.json';names=OUTPUTS|{'master-before/'+p for p in self.master}
        if seal_path.exists():
            self.sources(existing=True);seal=read_json(seal_path)
            require(seal['status']=='COMPLETE_DISCOVERY_POLICY_DIAGNOSTIC' and seal['fingerprint']==self.identity
                    and seal['inputs']==self.cfg['inputs'] and set(seal['artifacts'])==names,'invalid completion lineage')
            for n,spec in seal['artifacts'].items():require(pin(self.out/n)==spec,'output changed: '+n)
            print('VERIFIED_DISCOVERY_DIAGNOSTIC_NO_RECALCULATION');return
        self.sources();require(not self.out.exists() or not any(self.out.iterdir()),'partial output preserved')
        self.out.mkdir(parents=True,exist_ok=True);self.budget=Budget(self.out,self.cfg,self.identity);self.budget.thread.start()
        try:
            self.snapshot();self.score();self.evaluate();self.sources(existing=True);self.budget.guard();self.budget.close()
            artifacts={p.relative_to(self.out).as_posix():pin(p) for p in self.out.rglob('*') if p.is_file()}
            require(set(artifacts)==names,'unexpected output set')
            write_json(seal_path,{'status':'COMPLETE_DISCOVERY_POLICY_DIAGNOSTIC','fingerprint':self.identity,
                                 'inputs':self.cfg['inputs'],'artifacts':artifacts})
            print('COMPLETE_DISCOVERY_POLICY_DIAGNOSTIC')
        except BaseException as exc:
            self.budget.close();write_json(self.out/'failure.json',{'error':repr(exc),'fingerprint':self.identity});raise


if __name__=='__main__':Run().run()
