"""Package frozen genre candidates and verify adapter conformance; no fitting."""
from pathlib import Path
import json
import shutil
import numpy as np
import pandas as pd
from rec_ev_033_tastes import Budget, pin, read_json, require, sha, write_json
from rec_ev_038_clusters import Run as PreviousRun
from feelm_genre_candidates import GenreCandidates

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'docs/recommendation/experiments/rec-ev-039'
FILES={'design':PLAN/'README.md','config':PLAN/'config.json','runner':Path(__file__).resolve(),
       'adapter':ROOT/'scripts/feelm_genre_candidates.py','tests':ROOT/'scripts/tests/test_feelm_genre_candidates.py'}
for n in ('rec_ev_033_tastes.py','rec_ev_038_clusters.py'):
    FILES[n]=ROOT/'scripts'/n
METHODS=['RULE_GENRE','KM_GENRE']
OUTPUTS={'candidate-package.json','native-assignments.parquet','genre-composition.json',
         'conformance.json','contract-examples.json','GROUPS.md','budget.json','master-before/index.json'}


def fingerprint(): return {k:sha(p) for k,p in FILES.items()}


class Run:
    def __init__(self):
        self.identity=fingerprint(); self.cfg=read_json(FILES['config'])
        review=read_json(PLAN/'review.json')
        require(review['status']=='PASS' and review['fingerprint']==self.identity,'review missing/stale')
        self.out=ROOT/self.cfg['output_root']
        require(self.out.resolve()==ROOT/'outputs/recommendation-evidence/rec-ev-039','unexpected output path')
        self.paths={k:ROOT/v['path'] for k,v in self.cfg['inputs'].items()}
        self.previous=read_json(self.paths['result_review038'])
        self.master={p:v for p,v in self.previous['final_report_pins'].items()
                     if p.startswith('docs/recommendation/plans/service-policy-redesign/') or p=='docs/runbook/local-development.md'}

    def sources(self, existing=False):
        require(fingerprint()==self.identity,'code changed')
        for key,spec in self.cfg['inputs'].items():
            require(pin(self.paths[key])=={f:spec[f] for f in ['bytes','sha256']},'input changed: '+key)
        prev=PreviousRun();prev.sources();prev.verify_numeric()
        require(self.previous['status']=='PASS_AUDIT_COMPLETE','predecessor not reviewed')
        base=self.paths['numeric_seal038'].parent
        for n,spec in self.previous['phase_seals'].items():require(pin(base/n)==spec,'predecessor phase changed')
        for n,spec in self.previous['independently_verified_outputs'].items():require(pin(base/n)==spec,'previous result changed')
        for path,spec in self.previous['final_report_pins'].items():
            p=self.out/'master-before'/path if existing and path in self.master else ROOT/path
            require(pin(p)==spec,'previous report/snapshot changed: '+path)

    def snapshot(self):
        for path in self.master:
            target=self.out/'master-before'/path;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/path,target)
        write_json(self.out/'master-before/index.json',{'source_review':pin(self.paths['result_review038']),
                   'historical_master_pins':self.master,'current_master_may_advance':True})

    def calculate(self):
        meta=pd.read_parquet(self.paths['metadata']).sort_values('movie_id').reset_index(drop=True)
        z=np.load(self.paths['assignments038'],allow_pickle=False)
        feature=np.load(self.paths['feature_index038'],allow_pickle=False)
        model=np.load(self.paths['model038'],allow_pickle=False)
        ids=meta.movie_id.to_numpy()
        require(len(ids)==85517 and np.all(np.diff(ids)>0) and np.array_equal(ids,z['movie_ids'])
                and np.array_equal(ids,feature['movie_ids']),'movie identity mismatch')
        mask=np.array([str(v).startswith('genre:') for v in feature['vocabulary']])
        genres=[int(v.split(':')[1]) for v in feature['vocabulary'][mask]]
        cfg033=read_json(self.paths['config033'])
        rules=sorted(cfg033['genre_groups'])
        package={'package_id':'REC039_FROM_REC038','genre_ids':genres,'idf':feature['idf'][mask].tolist(),
                 'raw_centers':model['raw_centers'].tolist(),'raw_cluster_order':model['raw_cluster_order'].tolist(),
                 'rule_codes':rules,'rule_genre_groups':cfg033['genre_groups'],
                 'source_pins':{k:pin(self.paths[k]) for k in ['feature_index038','model038','config033']},
                 'official_flavor_mapping':None,'official_names_and_colors':None}
        predictor=GenreCandidates(package)
        write_json(self.out/'candidate-package.json',package)
        rows=[]; labels={}; presence=np.array([[int(g in set(seq)) for g in genres] for seq in meta.genre_ids],dtype=np.int8)
        for method in METHODS:
            own=[]
            for r in meta.itertuples(index=False):
                own.append({'movie_id':int(r.movie_id),**predictor.predict(r.genre_ids,method)})
            a=np.array([r['native_label'] for r in own]);expected=z['native'][list(z['methods']).index(method)]
            require(np.array_equal(a,expected),'adapter mismatch: '+method)
            require(all(not r['unknown_genre_ids'] for r in own),'unexpected catalogue unknown genres')
            labels[method]=a;rows+=own; self.budget.guard()
        pd.DataFrame(rows).to_parquet(self.out/'native-assignments.parquet',index=False)
        terms=pd.read_parquet(self.paths['terms'])
        names={int(r.term_key.split(':')[1]):str(r.display_text) for r in terms.itertuples(index=False) if r.term_key.startswith('genre:')}
        composition=[]; global_counts=presence.sum(axis=0)
        for method in METHODS:
            a=labels[method];supported=a>=0
            for k in range(8):
                inside=a==k;outside=supported&~inside;n=int(inside.sum());nout=int(outside.sum())
                counts=presence[inside].sum(axis=0);outside_counts=presence[outside].sum(axis=0)
                prefix='RULE' if method=='RULE_GENRE' else 'GENRE_KM'
                for j,g in enumerate(genres):
                    composition.append({'method':method,'native_label':k,'group_code':f'{prefix}_{k+1:02d}',
                        'genre_id':g,'genre_name':names[g],'within_count':int(counts[j]),'within_denominator':n,
                        'within_fraction':float(counts[j]/n),'outside_count':int(outside_counts[j]),
                        'outside_denominator':nout,'outside_fraction':float(outside_counts[j]/nout),
                        'catalogue_genre_count':int(global_counts[j])})
        write_json(self.out/'genre-composition.json',composition)
        examples=[]
        for title,gs in self.cfg['examples']:
            examples.append({'case':title,'input_genre_ids':gs,'results':[predictor.predict(gs,m) for m in METHODS]})
        write_json(self.out/'contract-examples.json',examples)
        conformance={'movies':len(ids),'assignment_rows':len(rows),'exact_matches':len(rows),
            'new_fits':0,'new_encoding':0,'ratings_decoded':False,'previous_128_rescored':False,
            'methods':{m:{'native_supported':int((a>=0).sum()),'unsupported':int((a<0).sum()),
                          'native_sizes':np.bincount(a[a>=0],minlength=8).tolist()} for m,a in labels.items()},
            'format_only_movies':int(sum(bool(len(gs)) and set(gs)=={10770} for gs in meta.genre_ids)),
            'composition_rows':len(composition),'official_flavor_mapping':False,'future_movie_quality_evaluated':False}
        write_json(self.out/'conformance.json',conformance)
        self.render(composition,rules,cfg033['genre_groups'],names)

    def render(self,rows,rules,groups,names):
        lines=['# REC039 — 두 후보의 실제 장르 구성','','상태: DRAFT — 고정 배정의 구성 설명. 공식 맛명·색상 계약이 아니다.','',
            '숫자는 그룹 내 해당 장르 영화 수/그룹 지원 영화 수다. 복수 장르여서 비율의 합은100%를 넘을 수 있다.',
            '아래 상위3개는 영화 비율 순(동률 장르ID)이며 c-TF-IDF 순위나 배정 규칙 자체와 다르다.',
            '순서가 같은 그룹 번호라도 두 방법의 의미는 다르다. 설명을 바꾼 뒤 REC038의128편을 재채점하지 않았다.','']
        for m in METHODS:
            lines += [f'## {m}','','| 연구 그룹 | 영화 수 | 실제 상위 장르 비율 | 배정 규칙 |',
                      '| --- | ---: | --- | --- |']
            for k in range(8):
                rs=sorted([r for r in rows if r['method']==m and r['native_label']==k],key=lambda r:(-r['within_count'],r['genre_id']))
                top=' · '.join(f"{r['genre_name']} {r['within_fraction']:.1%}" for r in rs[:3])
                rule=('첫 내용 장르: '+', '.join(names[g] for g in groups[rules[k]])) if m=='RULE_GENRE' else '고정 IDF/L2 장르 벡터의 최근접 centroid'
                lines.append(f"| {rs[0]['group_code']} | {rs[0]['within_denominator']:,} | {top} | {rule} |")
            lines += ['']
        lines += ['전체19장르의304개 수치 행은 genre-composition.json에 보존한다.',
                  'RULE의TV-only12편은 미지원이며 KM의해당12편은 형식 정보에 의한 배정임을 별도 표시한다.',
                  '두 후보의 영화별 단일 배정이 유지됐다는 적합성 검증이며 취향·추천 품질의 검증이 아니다.']
        (self.out/'GROUPS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

    def run(self):
        seal_path=self.out/'completion-seal.json'
        names=OUTPUTS|{'master-before/'+p for p in self.master}
        if seal_path.exists():
            self.sources(existing=True);seal=read_json(seal_path)
            require(seal['status']=='COMPLETE_ADAPTER_CONFORMANCE' and seal['fingerprint']==self.identity
                    and seal['inputs']==self.cfg['inputs'] and set(seal['artifacts'])==names,'invalid completion lineage')
            for n,spec in seal['artifacts'].items():require(pin(self.out/n)==spec,'output changed: '+n)
            print('VERIFIED_CANDIDATE_CONTRACT_NO_RECALCULATION');return
        self.sources();require(not self.out.exists() or not any(self.out.iterdir()),'partial output preserved')
        self.out.mkdir(parents=True,exist_ok=True)
        self.budget=Budget(self.out,self.cfg,self.identity);self.budget.thread.start()
        try:
            self.snapshot();self.calculate();self.sources(existing=True);self.budget.guard();self.budget.close()
            artifacts={p.relative_to(self.out).as_posix():pin(p) for p in self.out.rglob('*') if p.is_file()}
            require(set(artifacts)==names,'unexpected output set')
            write_json(seal_path,{'status':'COMPLETE_ADAPTER_CONFORMANCE','fingerprint':self.identity,
                                 'inputs':self.cfg['inputs'],'artifacts':artifacts})
            print('COMPLETE_ADAPTER_CONFORMANCE')
        except BaseException as exc:
            self.budget.close();write_json(self.out/'failure.json',{'error':repr(exc),'fingerprint':self.identity});raise


if __name__=='__main__':Run().run()
