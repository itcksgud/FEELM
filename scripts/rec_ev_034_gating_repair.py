"""One post-result eligibility alternative, reusing the fixed raw ALS500."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import rec_ev_034_supply as base

ROOT = base.ROOT
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-034/gating-repair'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json', 'runner': Path(__file__).resolve(),
         'tests': ROOT / 'scripts/tests/test_rec_ev_034_gating_repair.py', 'supply_helper': Path(base.__file__).resolve(),
         'state_helper': Path(base.policy.__file__).resolve(), 'resource_helper': Path(base.util.__file__).resolve()}
COMPARISON = ('eligibility-diagnostic.parquet', 'comparison.parquet', 'slots.parquet', 'summary.json')
OUTPUTS = ('role-seal.json', 'baseline-check.json', 'baseline-seal.json', *COMPARISON, 'comparison-seal.json', 'budget.json')
require, pin, read_json, write_json = base.require, base.pin, base.read_json, base.write_json


def fingerprint():
    return {key: base.util.sha(path) for key, path in FILES.items()}


def reviewed():
    value = read_json(PLAN / 'review.json'); actual = fingerprint()
    require(value['status'] == 'PASS' and value['fingerprint'] == actual, 'review absent or stale')
    return actual


def support_masks(labels, observed, weights, token_rows, inverted, count=8):
    local = labels[observed]
    positive = weights > 0
    assigned_positive = positive & (local >= 0)
    anchors = observed[assigned_positive]
    support = np.zeros(count, dtype=bool); support[local[assigned_positive]] = True
    experience = np.zeros(count, dtype=bool); experience[local[local >= 0]] = True
    assigned = labels >= 0; safe = np.maximum(labels, 0)
    taste = assigned & support[safe]
    connected = np.zeros(len(labels), dtype=bool)
    for token in set().union(*(token_rows[p] for p in anchors)) if len(anchors) else ():
        connected[inverted[token]] = True
    discovery = assigned & ~experience[safe] & ~support[safe] & connected
    return taste, discovery, anchors, {'positive_observations': int(positive.sum()),
        'assigned_positive_observations': int(assigned_positive.sum()), 'support_tastes': int(support.sum())}


def no_support_reason(original_support, positive_observations, assigned_positive):
    if original_support: return 'ORIGINAL_SUPPORTED'
    if not positive_observations: return 'NO_POSITIVE_OBSERVATIONS'
    if not assigned_positive: return 'ONLY_UNASSIGNED_POSITIVE'
    return 'MEAN_CANCELLATION'


def describe_slots(ids, positions, kinds, old_taste, old_anchors, new_anchors, token_rows):
    facts, new_t = [], 0
    new_d_requires_anchor = False
    for pos, kind in zip(positions, kinds):
        if kind == 'TASTE':
            new_t += int(not old_taste[pos])
        else:
            old_connection = any(token_rows[pos] & token_rows[p] for p in old_anchors)
            new_d_requires_anchor = not old_connection
            facts = [{'anchor_movie_id': int(ids[p]), 'field': field, 'id': int(tid)}
                     for p in sorted(new_anchors, key=lambda p: ids[p])
                     for field, tid in sorted(token_rows[pos] & token_rows[p])]
            require(bool(facts), 'selected D has no fact')
    return new_t, new_d_requires_anchor, facts


def summarize(frame, diagnostic, users):
    reports = []
    for policy, group in frame.groupby('policy', sort=True):
        at100 = group[group.stage == 'TOPN100']
        require(len(at100) == users * 2, 'missing users/variants')
        joined = at100.pivot(index='user_key', columns='variant', values='status')
        require(len(joined) == users and not joined.isna().any().any(), 'unpaired users')
        old, new = joined['MEAN'], joined['POSITIVE_OBSERVATION']
        transitions = joined.groupby(['MEAN', 'POSITIVE_OBSERVATION']).size()
        diag = diagnostic[diagnostic.policy == policy]
        reports.append({'policy': policy, 'users': users,
            'baseline_status_counts': old.value_counts().to_dict(), 'alternative_status_counts': new.value_counts().to_dict(),
            'transitions': [{'before': a, 'after': b, 'users': int(n)} for (a,b),n in transitions.items()],
            'T2_D1_recovered': int(((old != 'T2_D1') & (new == 'T2_D1')).sum()),
            'T2_D1_regressed': int(((old == 'T2_D1') & (new != 'T2_D1')).sum()),
            'original_no_support_reasons': diag[diag.original_support_tastes == 0].no_support_reason.value_counts().to_dict(),
            'new_T_from_nonpositive_mean_slots': int(at100[at100.variant == 'POSITIVE_OBSERVATION'].new_T_nonpositive_mean.sum()),
            'new_D_requires_new_anchor_users': int(at100[at100.variant == 'POSITIVE_OBSERVATION'].new_D_requires_anchor.sum()),
            'alternative500_status_counts': group[(group.stage == 'CANDIDATE500') & (group.variant == 'POSITIVE_OBSERVATION')].status.value_counts().to_dict()})
    return {'users': users, 'by_policy': reports, 'post_result_single_alternative': True,
            'T_eligibility_and_D_anchor_changed_together': True, 'new_ALS_scores': 0, 'new_model_fits': 0,
            'scope': 'FIXED_RAW500_N30_OBSERVED_INPUT_PROXY_SUPPLY', 'quality_evaluated': False,
            'product_adopted': False, 'A_B_winner_selected': False, 'final_K_selected': False}


class Run(base.Run):
    def __init__(self):
        self.identity = reviewed(); self.cfg = read_json(FILES['config']); self.budget = None; self.paths = {}
        c = self.cfg
        require(c['experiment'] == 'REC034_GATING_REPAIR' and c['expected_users'] == 2180 and c['expected_items'] == 85517
            and c['n'] == 30 and c['candidate_size'] == 500 and c['topn_size'] == 100
            and c['variants'] == ['MEAN','POSITIVE_OBSERVATION'], 'setting drift')
        self.root = ROOT / c['output_root']
        require(self.root.resolve() == (ROOT / 'outputs/recommendation-evidence/rec-ev-034/gating-repair').resolve(), 'output root')

    def guard(self):
        require(reviewed() == self.identity, 'review changed')
        if self.budget: self.budget.guard()

    def sources(self):
        super().sources()  # Hash-only lineage validation; no model arrays decoded.
        completion = read_json(self.paths['baseline_completion']); audit = read_json(self.paths['baseline_audit'])
        require(completion['status'] == 'COMPLETE' and audit['status'] == 'PASS'
            and audit['completion'] == pin(self.paths['baseline_completion']), 'baseline audit absent')
        for name, expected in completion['outputs'].items():
            require(pin(self.paths['baseline_completion'].parent / name) == expected, 'baseline output drift')
        for key in ('baseline_supply','baseline_slots','baseline_prefixes'):
            require(completion['outputs'][self.paths[key].name] == pin(self.paths[key]), 'baseline source mismatch')

    def load(self):
        self.seal('role-seal.json', (), sources=self.cfg['inputs'], users=2180, n=30,
            profile_values_decoded=False, ALS_scoring_allowed=False, evaluation_labels_allowed=False)
        with np.load(self.paths['prepared'], allow_pickle=False) as z:
            self.ids, self.prior = z['item_ids'].astype(np.int64), z['prior'].astype(np.float64)
        require(len(self.ids) == 85517 and (np.diff(self.ids)>0).all(), 'catalogue IDs')
        self.profiles = pd.read_parquet(self.paths['profiles']).sort_values('user_key').reset_index(drop=True)
        require(len(self.profiles) == 2180 and self.profiles.user_key.is_unique, 'user set')
        with np.load(self.paths['baseline_prefixes'], allow_pickle=False) as z:
            require(np.array_equal(z['user_keys'], self.profiles.user_key.to_numpy(dtype=str)), 'raw500 user order')
            movie_ids, lengths = z['movie_ids'], z['lengths']
        require(movie_ids.shape == (2180,500) and (lengths == 500).all() and np.isin(movie_ids,self.ids).all(), 'raw500 shape/IDs')
        self.raw = np.searchsorted(self.ids, movie_ids)
        require(all(len(set(row)) == 500 for row in self.raw), 'duplicate raw500')
        self.reference = pd.read_parquet(self.paths['baseline_supply']).set_index(['user_key','policy'])
        self.reference_slots = pd.read_parquet(self.paths['baseline_slots']).set_index(['user_key','policy','stage'])
        require(self.reference.index.is_unique and len(self.reference)==4360 and (self.reference.als_status == 'ACTIVE').all(), 'baseline users/activity')
        require(self.reference_slots.index.is_unique and len(self.reference_slots)==13080, 'baseline slots')
        assignment = pd.read_parquet(self.paths['assignments']).set_index('movie_id').loc[self.ids]
        codes = {'A_GENRE':self.cfg['A_codes'], 'B_KMEANS':self.cfg['B_codes']}
        self.labels = {'A_GENRE':np.array([codes['A_GENRE'].index(v) if v in codes['A_GENRE'] else -1 for v in assignment.genre_code]),
            'B_KMEANS':np.array([codes['B_KMEANS'].index(v) for v in assignment.semantic_code])}
        structure = pd.read_parquet(self.paths['structured']).set_index('movie_id').loc[self.ids]
        self.tokens,self.inverted = base.inverted_features([{'genres':r.genre_ids,'directors':r.director_ids,'keywords':r.keyword_ids}
                                                          for r in structure.itertuples(index=False)])
        self.requests = []
        for row in self.profiles.itertuples(index=False):
            movies, indices = np.asarray(row.profile_movie_ids), np.asarray(row.profile_rating_indices)
            require(len(movies)==len(set(movies))==len(indices)==30 and np.isin(movies,self.ids).all()
                and np.equal(indices,indices.astype(int)).all() and ((indices>=0)&(indices<10)).all(), 'input grid/scope')
            observed = np.searchsorted(self.ids,movies); weights = np.asarray(base.policy.relative_weights(((indices+1)/2).tolist(),self.prior.tolist()))
            excluded = np.zeros(len(self.ids),bool); excluded[observed]=True
            self.requests.append((row.user_key,observed,weights,excluded))

    def baseline(self):
        checks = 0
        for u,(key,observed,weights,excluded) in enumerate(self.requests):
            for name,labels in self.labels.items():
                t,d,a,info = base.eligibility(labels,observed,weights,self.tokens,self.inverted)
                ref = self.reference.loc[(key,name)]
                for field in info: require(info[field] == ref[field], 'baseline eligibility mismatch')
                require(int(t.sum())==ref.catalogue_T and int(d.sum())==ref.catalogue_D, 'baseline catalogue mismatch')
                stages,first,typed = base.stage_orders(self.raw[u],t,d)
                for stage in ('CANDIDATE500','TOPN100'):
                    status,pos,kinds,tc,dc=base.select_slots(stages[stage],t,d,excluded)
                    suffix='500' if stage=='CANDIDATE500' else '100'
                    old=self.reference_slots.loc[(key,name,stage)]
                    require(status==ref['status'+suffix]==old.status and tc==ref['T'+suffix] and dc==ref['D'+suffix]
                        and np.array_equal(self.ids[pos],old.movie_ids) and kinds==list(old.types), 'baseline slot mismatch')
                    _,_,facts=describe_slots(self.ids,pos,kinds,t,a,a,self.tokens)
                    require(facts==json.loads(old.D_connection_facts_json),'baseline connection mismatch')
                    checks+=1
            if (u+1)%200==0: self.guard()
        write_json(self.root/'baseline-check.json',{'users':len(self.requests),'policy_cells':len(self.requests)*2,
            'stage_slot_rows_matched':checks,'mismatches':0,'new_ALS_scores':0,'alternative_calculated':False})
        self.seal('baseline-seal.json',('baseline-check.json',),role_seal=pin(self.root/'role-seal.json'),sources=self.cfg['inputs'])

    def compare(self):
        self.verify('baseline-seal.json',('baseline-check.json',),role_seal=pin(self.root/'role-seal.json'),sources=self.cfg['inputs'])
        rows,slots,diagnostics=[],[],[]
        for u,(key,observed,weights,excluded) in enumerate(self.requests):
            for name,labels in self.labels.items():
                old_t,old_d,old_a,old_info=base.eligibility(labels,observed,weights,self.tokens,self.inverted)
                new_t,new_d,new_a,new_info=support_masks(labels,observed,weights,self.tokens,self.inverted)
                require(not (old_t & ~new_t).any() and not (old_d & ~new_d).any() and set(old_a)<=set(new_a),'non-monotone pre-cutoff eligibility')
                reason=no_support_reason(old_info['positive_tastes'],new_info['positive_observations'],new_info['assigned_positive_observations'])
                diagnostics.append({'user_key':key,'policy':name,'original_support_tastes':old_info['positive_tastes'],**new_info,
                    'no_support_reason':reason,'old_catalogue_T':int(old_t.sum()),'new_catalogue_T':int(new_t.sum()),
                    'old_catalogue_D':int(old_d.sum()),'new_catalogue_D':int(new_d.sum()),'old_anchors':len(old_a),'new_anchors':len(new_a)})
                for variant,t,d,anchors in [('MEAN',old_t,old_d,old_a),('POSITIVE_OBSERVATION',new_t,new_d,new_a)]:
                    stages,first,typed=base.stage_orders(self.raw[u],t,d)
                    for stage in ('CANDIDATE500','TOPN100'):
                        status,pos,kinds,tc,dc=base.select_slots(stages[stage],t,d,excluded)
                        new_t_count,new_anchor_needed,facts=describe_slots(self.ids,pos,kinds,old_t,old_a,anchors,self.tokens)
                        rows.append({'user_key':key,'policy':name,'variant':variant,'stage':stage,'status':status,
                            'T_supply':tc,'D_supply':dc,'new_T_nonpositive_mean':new_t_count,'new_D_requires_anchor':new_anchor_needed})
                        slots.append({'user_key':key,'policy':name,'variant':variant,'stage':stage,'status':status,
                            'movie_ids':self.ids[pos].tolist(),'types':kinds,'D_connection_facts_json':json.dumps(facts,sort_keys=True)})
            if (u+1)%200==0:
                self.guard(); print({'phase':'SINGLE_GATE_ALTERNATIVE','users':u+1,'seconds':round(self.budget.elapsed(),2)},flush=True)
        frame,diag=pd.DataFrame(rows),pd.DataFrame(diagnostics)
        frame.to_parquet(self.root/'comparison.parquet',index=False);diag.to_parquet(self.root/'eligibility-diagnostic.parquet',index=False)
        pd.DataFrame(slots).to_parquet(self.root/'slots.parquet',index=False)
        write_json(self.root/'summary.json',summarize(frame,diag,len(self.requests)))
        self.seal('comparison-seal.json',COMPARISON,baseline_seal=pin(self.root/'baseline-seal.json'),sources=self.cfg['inputs'])

    def run(self):
        require(not (self.root/'failure.json').exists(),'failed execution; preserve without retry')
        if (self.root/'completion-seal.json').exists():
            self.sources();self.verify('completion-seal.json',OUTPUTS,sources=self.cfg['inputs'])
            self.verify('role-seal.json',(),sources=self.cfg['inputs'])
            self.verify('baseline-seal.json',('baseline-check.json',),role_seal=pin(self.root/'role-seal.json'),sources=self.cfg['inputs'])
            self.verify('comparison-seal.json',COMPARISON,baseline_seal=pin(self.root/'baseline-seal.json'),sources=self.cfg['inputs'])
            print('VERIFIED_EXISTING_COMPLETION_NO_RECALCULATION');return
        require(not self.root.exists() or not any(self.root.iterdir()),'partial execution; preserve without retry')
        self.root.mkdir(parents=True,exist_ok=True);self.budget=base.util.Budget(self.root,self.cfg,self.identity)
        self.budget.save();self.budget.thread.start()
        try:
            self.sources();self.load();self.baseline();self.compare();self.sources();self.guard();self.budget.close();self.guard()
            self.seal('completion-seal.json',OUTPUTS,sources=self.cfg['inputs'],new_ALS_scores=0,new_model_fits=0)
            print({'phase':'GATING_REPAIR_COMPLETE','seconds':round(self.budget.elapsed(),2)},flush=True)
        except Exception as exc:
            self.budget.close()
            if not (self.root/'failure.json').exists(): write_json(self.root/'failure.json',{'status':'FAILED','fingerprint':self.identity,'error_type':type(exc).__name__,'error':str(exc)})
            raise


if __name__=='__main__':
    Run().run()
