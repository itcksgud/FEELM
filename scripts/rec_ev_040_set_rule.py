"""Apply one pre-reviewed full-genre rule and describe its behavior; no fitting."""
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
from pathlib import Path
import hashlib
import shutil
import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score
from rec_ev_033_tastes import Budget, pin, read_json, require, sha, write_json
from rec_ev_039_candidate_contract import Run as PreviousRun
from feelm_genre_candidates import GenreCandidates
from feelm_genre_set_rule import GenreSetRule, METHOD, POLICY

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'docs/recommendation/experiments/rec-ev-040'
FILES = {'design':PLAN/'README.md', 'config':PLAN/'config.json', 'runner':Path(__file__).resolve(),
         'adapter':ROOT/'scripts/feelm_genre_set_rule.py', 'tests':ROOT/'scripts/tests/test_feelm_genre_set_rule.py'}
for name in ('rec_ev_039_candidate_contract.py','feelm_genre_candidates.py','rec_ev_033_tastes.py','rec_ev_038_clusters.py'):
    FILES[name] = ROOT/'scripts'/name
METHODS = ['RULE_GENRE', METHOD, 'KM_GENRE']
OUTPUTS = {'rule-package.json','native-assignments.parquet','genre-composition.json','comparison.json',
           'contingencies.json','perturbations.parquet','contract-examples.json','catalogue-examples.json',
           'GROUPS.md','budget.json','master-before/index.json'}


def fingerprint(): return {k:sha(p) for k,p in FILES.items()}


class Run:
    def __init__(self):
        self.identity = fingerprint()
        self.cfg = read_json(FILES['config'])
        review = read_json(PLAN/'review.json')
        require(review['status']=='PASS' and review['fingerprint']==self.identity,'review missing/stale')
        self.out = ROOT/self.cfg['output_root']
        require(self.out.resolve()==ROOT/'outputs/recommendation-evidence/rec-ev-040','unexpected output path')
        self.paths = {k:ROOT/v['path'] for k,v in self.cfg['inputs'].items()}
        self.previous = read_json(self.paths['review039'])
        self.master = {p:v for p,v in self.previous['final_report_pins'].items()
                       if p.startswith('docs/recommendation/plans/service-policy-redesign/') or p=='docs/runbook/local-development.md'}
        require(len(self.master)==5,'expected five historical master documents')

    def sources(self, existing=False):
        require(fingerprint()==self.identity,'code changed')
        for k,spec in self.cfg['inputs'].items():
            require(pin(self.paths[k])=={f:spec[f] for f in ['bytes','sha256']},'source changed: '+k)
        require(self.previous['status']=='PASS_AUDIT_COMPLETE','previous result not reviewed')
        prev = PreviousRun(); prev.sources(existing=True)
        seal = read_json(self.paths['seal039'])
        require(seal['status']=='COMPLETE_ADAPTER_CONFORMANCE' and seal['fingerprint']==prev.identity
                and pin(self.paths['seal039'])==self.previous['completion_seal'],'previous completion mismatch')
        for n,spec in seal['artifacts'].items():
            require(pin(self.paths['seal039'].parent/n)==spec,'previous output changed: '+n)
        for path,spec in self.previous['final_report_pins'].items():
            p = self.out/'master-before'/path if existing and path in self.master else ROOT/path
            require(pin(p)==spec,'previous report/snapshot changed: '+path)

    def snapshot(self):
        for path in self.master:
            target=self.out/'master-before'/path; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/path,target)
        write_json(self.out/'master-before/index.json',{'source_review':pin(self.paths['review039']),
                   'historical_master_pins':self.master,'current_master_may_advance':True})

    def calculate(self):
        meta = pd.read_parquet(self.paths['metadata'],columns=['movie_id','title','genre_ids']).sort_values('movie_id').reset_index(drop=True)
        ids = meta.movie_id.to_numpy()
        require(len(ids)==85517 and np.all(np.diff(ids)>0),'catalogue identity invalid')
        old_package = read_json(self.paths['package039'])
        package = {'package_id':'REC040_GENRE_SET_RULE','policy':POLICY,
                   'rule_codes':old_package['rule_codes'], 'rule_genre_groups':old_package['rule_genre_groups'],
                   'source_package_pin':pin(self.paths['package039']), 'official_flavor_mapping':None}
        write_json(self.out/'rule-package.json',package)
        new = GenreSetRule(package); old = GenreCandidates(old_package)
        genres = old_package['genre_ids']
        require(set(genres)==new.content|{10770},'unexpected content vocabulary')
        names = {int(r.term_key.split(':')[1]):str(r.display_text) for r in pd.read_parquet(self.paths['terms']).itertuples(index=False)
                 if r.term_key.startswith('genre:')}
        seqs = [tuple(map(int,x)) for x in meta.genre_ids]
        counts = Counter(seqs)

        @lru_cache(maxsize=None)
        def predict(method, seq):
            return new.predict(seq) if method==METHOD else old.predict(seq,method)

        labels = {m:np.array([predict(m,seq)['native_label'] for seq in seqs],dtype=np.int64) for m in METHODS}
        prior = pd.read_parquet(self.paths['assignments039'])
        for m in ['RULE_GENRE','KM_GENRE']:
            p = prior[prior.method==m].sort_values('movie_id')
            require(np.array_equal(p.movie_id.to_numpy(),ids) and np.array_equal(p.native_label.to_numpy(),labels[m]),'old assignments changed: '+m)
        own = [{'movie_id':int(mid),**predict(METHOD,seq)} for mid,seq in zip(ids,seqs)]
        require(all(not r['unknown_genre_ids'] for r in own),'unexpected catalogue unknown')
        require(all((r['native_label']==-1 and r['group_code'] is None) or
                    (0<=r['native_label']<8 and r['tie_count']>=1 and r['native_label'] in r['top_native_labels']) for r in own),'invalid new assignment')
        pd.DataFrame(own).to_parquet(self.out/'native-assignments.parquet',index=False)
        self.budget.guard()

        presence = np.array([[g in seq for g in genres] for seq in seqs],dtype=np.int8)
        composition = []
        for m,a in labels.items():
            for k in range(8):
                inside=a==k; outside=(a>=0)&~inside
                n=int(inside.sum()); nout=int(outside.sum())
                cin=presence[inside].sum(axis=0); cout=presence[outside].sum(axis=0)
                for j,g in enumerate(genres):
                    composition.append({'method':m,'native_label':k,'genre_id':g,'genre_name':names[g],
                        'within_count':int(cin[j]),'within_denominator':n,'within_fraction':float(cin[j]/n) if n else None,
                        'outside_count':int(cout[j]),'outside_denominator':nout,'outside_fraction':float(cout[j]/nout) if nout else None})
        write_json(self.out/'genre-composition.json',composition)

        diagnostics = {}; perturbations = []
        for m,a in labels.items():
            reverse_changed=duplicate_changed=variant_count=variant_changed=target_movies=changed_movies=0
            signatures=defaultdict(lambda: {'labels':set(),'movies':0})
            for seq,weight in counts.items():
                original=predict(m,seq)['native_label']
                reverse_changed += weight*(predict(m,tuple(reversed(seq)))['native_label']!=original)
                duplicate_changed += weight*(predict(m,seq+seq)['native_label']!=original)
                signature=tuple(sorted(set(seq)))
                signatures[signature]['labels'].add(original); signatures[signature]['movies']+=weight
                content=sorted(set(seq)&new.content)
                if len(content)<2: continue
                target_movies+=weight; any_changed=False
                for g in content:
                    reduced=tuple(v for v in seq if v!=g)
                    result=predict(m,reduced)['native_label']
                    require(result>=0,'deletion removed all support unexpectedly')
                    changed=result!=original
                    variant_count+=weight; variant_changed+=weight*changed; any_changed|=changed
                    perturbations.append({'method':m,'original_genre_ids':list(seq),'removed_genre_id':g,
                        'original_label':original,'reduced_label':result,'movie_weight':weight,'changed':changed})
                changed_movies+=weight*any_changed
            sizes=np.bincount(a[a>=0],minlength=8).tolist()
            n=int((a>=0).sum())
            diagnostics[m]={'supported':n,'unsupported':len(a)-n,'native_sizes':sizes,
                'occupied_groups':sum(x>0 for x in sizes),'largest_group_fraction':max(sizes)/n if n else None,
                'unsupported_reasons':dict(Counter(predict(m,s)['status'] for s in seqs if predict(m,s)['native_label']<0)),
                'reversal_changed_movies':reverse_changed,'duplicate_changed_movies':duplicate_changed,
                'observed_genre_sets':len(signatures),
                'inconsistent_genre_sets':sum(len(v['labels'])>1 for v in signatures.values()),
                'movies_in_inconsistent_sets':sum(v['movies'] for v in signatures.values() if len(v['labels'])>1),
                'deletion_target_movies':target_movies,'deletion_changed_movies':changed_movies,
                'deletion_variants':variant_count,'deletion_changed_variants':variant_changed,
                'deletion_change_fraction':variant_changed/variant_count if variant_count else None}
            self.budget.guard()
        require(diagnostics[METHOD]['reversal_changed_movies']==diagnostics[METHOD]['duplicate_changed_movies']==
                diagnostics[METHOD]['inconsistent_genre_sets']==0,'set rule invariance failed')
        pd.DataFrame(perturbations).to_parquet(self.out/'perturbations.parquet',index=False)

        common=np.logical_and.reduce([a>=0 for a in labels.values()])
        pairs=[]; contingencies=[]
        for a,b in combinations(METHODS,2):
            table=np.zeros((8,8),dtype=int)
            np.add.at(table,(labels[a][common],labels[b][common]),1)
            for ka in range(8):
                for kb in range(8):
                    contingencies.append({'left':a,'right':b,'left_label':ka,'right_label':kb,'movies':int(table[ka,kb])})
            pairs.append({'left':a,'right':b,'common_supported_movies':int(common.sum()),
                          'adjusted_rand_index':float(adjusted_rand_score(labels[a][common],labels[b][common]))})
        write_json(self.out/'contingencies.json',contingencies)
        tie_counts=Counter(r['tie_count'] for r in own if r['supported'])
        summary={'movies':len(ids),'original_assignments_reproduced':2*len(ids),'new_assignment_rows':len(own),
                 'methods':diagnostics,'common_supported_movies':int(common.sum()),'pairs':pairs,
                 'old_rule_to_set_rule_changed_movies':int(((labels['RULE_GENRE']!=labels[METHOD])&common).sum()),
                 'new_rule_tied_movies':sum(n for k,n in tie_counts.items() if k>1),
                 'new_rule_tie_count_histogram':dict(tie_counts),
                 'composition_rows':len(composition),'contingency_rows':len(contingencies),
                 'weighted_perturbation_rows':len(perturbations),
                 'format_only_movies':sum(set(seq)=={10770} for seq in seqs),
                 'new_fits':0,'ratings_decoded':False,'previous_128_rescored':False,
                 'recommendation_slots_executed':False,'recommendation_quality_evaluated':False,
                 'official_flavor_mapping':False}
        write_json(self.out/'comparison.json',summary)
        examples=[{'case':name,'input_genre_ids':gs,'results':[predict(m,tuple(gs)) for m in METHODS]}
                  for name,gs in self.cfg['examples']]
        write_json(self.out/'contract-examples.json',examples)
        selections=[]
        def hash_key(i): return hashlib.sha256(f"{self.cfg['example_hash_prefix']}{int(ids[i])}".encode()).hexdigest()
        def example(i,scope):
            return {'scope':scope,'movie_id':int(ids[i]),'title':str(meta.iloc[i].title),
                    'genre_ids':list(seqs[i]),'genre_names':[names[g] for g in seqs[i]],
                    'results':[predict(m,seqs[i]) for m in METHODS]}
        for k in range(8):
            candidates=sorted(np.flatnonzero(labels[METHOD]==k),key=hash_key)
            selections += [example(i,f'new_group_{k+1:02d}') for i in candidates[:self.cfg['examples_per_group']]]
        changed=np.flatnonzero(common&(labels[METHOD]!=labels['RULE_GENRE']))
        selections += [example(i,'changed_from_original_rule') for i in sorted(changed,key=hash_key)[:self.cfg['changed_examples']]]
        write_json(self.out/'catalogue-examples.json',selections)
        self.render(summary,composition,selections,package)

    def render(self,summary,composition,examples,package):
        lines=['# REC040 — 전체 장르 규칙의 실제 적용','','상태: DRAFT — 개발·동작 진단. 추천 품질 순위가 아니다.','',
               '| 방법 | 내용 장르 포함 지원 | 전체 배정 | 순서 반전 변화 | 장르 하나 제거 시 변화 |',
               '| --- | ---: | ---: | ---: | ---: |']
        for m in METHODS:
            d=summary['methods'][m]
            lines.append(f"| {m} | {summary['common_supported_movies']:,} | {d['supported']:,} | {d['reversal_changed_movies']:,} | {d['deletion_changed_variants']:,}/{d['deletion_variants']:,} ({d['deletion_change_fraction']:.1%}) |")
        lines += ['', 'KM의 TV-only 배정12편은 내용 장르 지원으로 세지 않는다. 삭제 변화가 적을수록 좋은 분류라는 뜻도 아니다.',
                  f"새 규칙의 지원 영화 중 정확 동점은 {summary['new_rule_tied_movies']:,}편이며 고정 코드 순서로 하나를 고른다.",
                  '', '| 새 그룹 | 고정 의미 코드 | 영화 수 | 실제 상위 장르 비율 |', '| --- | --- | ---: | --- |']
        for k,code in enumerate(package['rule_codes']):
            rows=sorted([r for r in composition if r['method']==METHOD and r['native_label']==k],key=lambda r:(-r['within_count'],r['genre_id']))
            top=' · '.join(f"{r['genre_name']} {r['within_fraction']:.1%}" if r['within_fraction'] is not None else f"{r['genre_name']} 미지원" for r in rows[:3])
            lines.append(f"| GENRE_SET_{k+1:02d} | {code} | {rows[0]['within_denominator']:,} | {top} |")
        lines += ['', '비율은 입력 장르를 다시 세어 설명한 값이며 품질의 독립 증거가 아니다. 복수 장르의 합은100%를 넘을 수 있다.',
                  '', '## 고정 hash 영화 사례', '', '선택된 사례의 배정·장르를 확인하는 자료이며 사람 취향 정답이 아니다.', '',
                  '| 선택 범위 | 영화 | 입력 장르 순서 | 기존 RULE | 새 RULE | KM | 새 RULE 동점 후보 수 |',
                  '| --- | --- | --- | --- | --- | --- | ---: |']
        for row in examples:
            rs=row['results']; title=row['title'].replace('|','/').replace('\n',' ')
            labels=[r['group_code'] or '미지원' for r in rs]
            lines.append(f"| {row['scope']} | {row['movie_id']} {title} | {' → '.join(row['genre_names'])} | {' | '.join(labels)} | {rs[1]['tie_count']} |")
        (self.out/'GROUPS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

    def run(self):
        seal_path=self.out/'completion-seal.json'
        names=OUTPUTS|{'master-before/'+p for p in self.master}
        if seal_path.exists():
            self.sources(existing=True); seal=read_json(seal_path)
            require(seal['status']=='COMPLETE_SET_RULE_DIAGNOSTIC' and seal['fingerprint']==self.identity
                    and seal['inputs']==self.cfg['inputs'] and set(seal['artifacts'])==names,'invalid completion lineage')
            for n,spec in seal['artifacts'].items(): require(pin(self.out/n)==spec,'output changed: '+n)
            print('VERIFIED_SET_RULE_COMPLETION_NO_RECALCULATION'); return
        self.sources(); require(not self.out.exists() or not any(self.out.iterdir()),'partial output preserved')
        self.out.mkdir(parents=True,exist_ok=True)
        self.budget=Budget(self.out,self.cfg,self.identity); self.budget.thread.start()
        try:
            self.snapshot(); self.calculate(); self.sources(existing=True); self.budget.guard(); self.budget.close()
            artifacts={p.relative_to(self.out).as_posix():pin(p) for p in self.out.rglob('*') if p.is_file()}
            require(set(artifacts)==names,'unexpected output set')
            write_json(seal_path,{'status':'COMPLETE_SET_RULE_DIAGNOSTIC','fingerprint':self.identity,
                                 'inputs':self.cfg['inputs'],'artifacts':artifacts})
            print('COMPLETE_SET_RULE_DIAGNOSTIC')
        except BaseException as exc:
            self.budget.close(); write_json(self.out/'failure.json',{'error':repr(exc),'fingerprint':self.identity}); raise


if __name__=='__main__': Run().run()
