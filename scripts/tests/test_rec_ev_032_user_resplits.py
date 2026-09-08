import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rec_ev_032_user_resplits as mod
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


class ResplitTests(unittest.TestCase):
    def test_roles_match_independent_hash_reference(self):
        dev, eligible = list(range(1, 101)), list(range(1, 31))
        train, ev = mod.select_roles(dev, eligible, 17, 70, 20)
        expected_eval = sorted(eligible, key=lambda u: (hashlib.sha256(f'rec032-resplit-eval-v1|17|{u}'.encode()).digest(),u))[:20]
        expected_train = sorted(set(dev)-set(expected_eval), key=lambda u: (hashlib.sha256(f'rec032-resplit-train-v1|17|{u}'.encode()).digest(),u))[:70]
        self.assertEqual(set(ev),set(expected_eval)); self.assertEqual(set(train),set(expected_train))
        self.assertFalse(set(train)&set(ev))
        np.testing.assert_array_equal(train,np.sort(train))
        self.assertEqual(list(map(mod.base.user_key,ev)), sorted(map(mod.base.user_key,ev)))

    def test_role_input_order_invariant(self):
        a=mod.select_roles(range(1,101),range(1,31),17,70,20)
        b=mod.select_roles(reversed(range(1,101)),reversed(range(1,31)),17,70,20)
        for x,y in zip(a,b): np.testing.assert_array_equal(x,y)

    def test_seed_changes_roles_without_same_round_leak(self):
        a=mod.select_roles(range(1,101),range(1,31),17,70,20)
        b=mod.select_roles(range(1,101),range(1,31),18,70,20)
        self.assertNotEqual(set(a[0]),set(b[0])); self.assertNotEqual(set(a[1]),set(b[1]))
        self.assertFalse(set(b[0])&set(b[1]))
        self.assertTrue(set(a[1])&set(b[0]))  # Cross-round role changes are intentional.

    def test_roles_reject_capacity_or_outside_development(self):
        with self.assertRaisesRegex(RuntimeError,'capacity'): mod.select_roles([1,2],[2,3],1,1,1)
        with self.assertRaisesRegex(RuntimeError,'capacity'): mod.select_roles([1,2],[1,2],1,2,1)

    def test_development_keeps_outer_boundary(self):
        with patch.object(mod.base,'bucket',side_effect=[0,0,8000]): self.assertFalse(mod.development_user(1))
        with patch.object(mod.base,'bucket',side_effect=[0,0,9000]): self.assertFalse(mod.development_user(1))
        with patch.object(mod.base,'bucket',side_effect=[0,0,7999]): self.assertTrue(mod.development_user(1))
        self.assertFalse(mod.development_user(0)); self.assertFalse(mod.development_user(200949))

    def test_actual_training_rows_checked_not_just_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'train.parquet'
            pq.write_table(pa.table({'user_id':[1,1,2,2]}),p)
            self.assertEqual(mod.scan_training_users(p,[1,2],[3]),(4,2,0))
            with self.assertRaisesRegex(RuntimeError,'role violation'): mod.scan_training_users(p,[1,2],[2])
            with self.assertRaisesRegex(RuntimeError,'role violation'): mod.scan_training_users(p,[1,2,3],[4])

    def test_training_null_user_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'train.parquet';pq.write_table(pa.table({'user_id':[1,None]}),p)
            with self.assertRaisesRegex(RuntimeError,'null'):mod.scan_training_users(p,[1],[2])

    def test_extract_uses_only_round_train_and_matching_p0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); archive=root/'source.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('ml-32m/ratings.csv', 'userId,movieId,rating,timestamp\n1,10,0.5,ignored\n1,20,5.0,ignored\n2,10,4.0,ignored\n2,30,3.0,ignored\n3,NOT_PARSED,NOT_PARSED,NOT_PARSED\n')
            run=mod.Run.__new__(mod.Run);run.root=root;run.paths={'archive':archive};run.ids=np.array([10,20,30,40])
            run.cfg={'max_user_id':3,'max_movie_id':40,'expected_raw_rows':5}
            run.train_ids=np.array([[1,2]]);run.eval_ids=np.array([[3]]);run.log=lambda *a,**k:None
            stat=run.extract(0)
            self.assertEqual(stat['training_rows'],4);self.assertEqual(stat['evaluation_users_in_training_rows'],0)
            with np.load(root/'r0/bayes.npz') as z:
                self.assertEqual(float(z['global_mean']),3.125)
                np.testing.assert_array_equal(z['counts'],[2,1,1,0])
                np.testing.assert_array_equal(z['sums'],[4.5,5.,3.,0.])
                np.testing.assert_allclose(z['bayes'],(np.array([4.5,5.,3.,0.])+50*3.125)/(np.array([2,1,1,0])+50))

    def test_repeated_benefit_requires_all_intervals(self):
        self.assertEqual(mod.across_splits([1,1,1],['BETTER_DIRECTION']*3,'MEAN_Q'),'REPEATED_BENEFIT')
        self.assertEqual(mod.across_splits([1,1,1],['BETTER_DIRECTION','UNDECIDED','BETTER_DIRECTION'],'MEAN_Q'),'NOT_ESTABLISHED_ACROSS_ALL_SPLITS')
        self.assertEqual(mod.across_splits([-1,-1,-1],['WORSE_DIRECTION']*3,'MEAN_Q'),'REPEATED_HARM')

    def test_sign_variation_not_hidden_by_average(self):
        self.assertEqual(mod.across_splits([2,-1,2],['UNDECIDED']*3,'MEAN_Q'),'SPLIT_SENSITIVE_DIRECTION')
        self.assertEqual(mod.across_splits([-.1,.1,-.1],['UNDECIDED']*3,'HARM20'),'SPLIT_SENSITIVE_DIRECTION')
        self.assertEqual(mod.across_splits([0,0,0],['UNDECIDED']*3,'MEAN_Q'),'NOT_ESTABLISHED_ACROSS_ALL_SPLITS')

    def test_seal_requires_complete_output_set_and_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=mod.Run.__new__(mod.Run);run.root=Path(tmp);run.identity={};run.guard=lambda:None
            (run.root/'a').write_text('fixture')
            run.seal('seal.json',['a'],source='one')
            run.validate_seal('seal.json',['a'],{'source':'one'})
            with self.assertRaisesRegex(RuntimeError,'seal drift'):run.validate_seal('seal.json',['a','b'])
            with self.assertRaisesRegex(RuntimeError,'dependency'):run.validate_seal('seal.json',['a'],{'source':'two'})
            (run.root/'a').write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'output drift'):run.validate_seal('seal.json',['a'])

    def test_labels_cannot_open_without_all_scores_sealed(self):
        run=mod.Run.__new__(mod.Run)
        run.validate_seal=lambda *a,**k:mod.base.require(False,'not sealed')
        run.root=Path('missing-synthetic-directory')
        with patch.object(mod.base,'pin',return_value={}):
            with patch.object(mod.pd,'read_parquet') as reader:
                with self.assertRaisesRegex(RuntimeError,'not sealed'):run.evaluate()
                reader.assert_not_called()

    def test_27_family_bootstrap_matches_independent_counts(self):
        rng=np.random.default_rng(9);points=rng.normal(size=(13,3,3));values=np.repeat(points[...,None],2,axis=-1)
        got=mod.base.ci_bounds(values,200,17,.05,27)
        rng=np.random.default_rng(17); draws=rng.integers(0,13,size=(200,13))
        means=np.array([np.bincount(row,minlength=13)@points.reshape(13,9)/13 for row in draws]).reshape(200,3,3)
        expected=np.stack([np.quantile(means,.05/54,axis=0),np.quantile(means,1-.05/54,axis=0)],axis=-1)
        np.testing.assert_allclose(got,expected,atol=1e-15,rtol=0)

    def test_budget_guard_persists_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            budget=mod.Budget(Path(tmp),{}, {'max_seconds':1,'max_process_tree_bytes':1})
            with patch.object(budget,'sample',side_effect=lambda:setattr(budget,'peak',2)):
                with self.assertRaisesRegex(RuntimeError,'resource limit'):budget.guard()
            self.assertEqual(mod.base.read_json(Path(tmp)/'failure.json')['status'],'RESOURCE_LIMIT')
            self.assertEqual(mod.base.read_json(Path(tmp)/'budget.json')['peak_tree_rss_bytes'],2)

    def test_three_round_synthetic_score_and_evaluate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=mod.Run.__new__(mod.Run)
            run.root=root;run.identity={'test':'synthetic'};run.guard=lambda:None;run.log=lambda *a,**k:None
            run.validate_seal=lambda *a,**k:None
            run.cfg={'als':{'rank':1,'reg':.1},'ns':[0,1,2,3],'policies':['P0','A0_RAW_ALS_ONLY'],
                'bootstrap_repeats':200,'bootstrap_seed':17,'alpha':.05,'family_size':27,'experiment':'SYNTHETIC','evaluation_users_per_round':2}
            run.ids=np.arange(10,90,10);run.keys=np.array(['fixture_a','fixture_b'])
            run.eval_keys=np.array([run.keys]*3);run.targets={k:np.array([40,50,60]) for k in run.keys}
            run.pool=pd.DataFrame({'user_key':run.keys,'cohort':['SELECTION','REPLICATION']})
            run.profiles=pd.DataFrame({'user_key':run.keys,'profile_movie_ids':[[10,20,30]]*2,'profile_rating_indices':[[9,8,7],[1,2,3]]}).set_index('user_key')
            for name in ('role-seal.json','train-seal.json','prepare-seal.json'): (root/name).write_text('{}')
            for r in range(3):
                (root/f'r{r}').mkdir()
                np.savez_compressed(root/f'r{r}/bayes.npz',item_ids=run.ids,bayes=np.array([0,0,0,4,5,3,2,1],dtype=float))
                np.savez_compressed(root/f'r{r}/factors.npz',item_ids=run.ids,factors=np.array([[1],[2],[3],[3],[2],[1],[0],[-1]],dtype=float))
            run.score()
            run.expected={('SELECTION','R0','fixture_a'):{40,50,60},('REPLICATION','R0','fixture_b'):{40,50,60}}
            run.paths={}
            for cohort,key,indices in [('SELECTION','fixture_a',[9,5,1]),('REPLICATION','fixture_b',[3,7,9])]:
                h=[2]*10
                row={'outer':'R0','user_key':key,'target_movie_ids':[40,50,60],'target_rating_indices':indices,
                    'target_q':[mod.base.q_from_hist(i,h) for i in indices],'full_history_count':20,'full_history_histogram':h}
                p=root/f'{cohort}.parquet';pd.DataFrame([row]).to_parquet(p,index=False);run.paths[cohort.lower()+'_labels']=p
            run.evaluate()
            result=mod.base.read_json(root/'metrics.json')
            self.assertEqual(len(result['paired_differences']),27)
            self.assertEqual(len(result['metrics']),24)
            self.assertEqual(result['unique_evaluation_users'],2)
            self.assertEqual(result['round_user_occurrences'],6)
            self.assertFalse(result['pooled_independent_user_claim'])


if __name__=='__main__':unittest.main()
