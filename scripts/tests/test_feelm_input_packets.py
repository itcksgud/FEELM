import copy
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
SPEC=importlib.util.spec_from_file_location('packets',SCRIPTS/'feelm_input_packets.py')
m=importlib.util.module_from_spec(SPEC);sys.modules[SPEC.name]=m;SPEC.loader.exec_module(m)


def snapshot():
    return {'input_version':4,'id_namespace':'TMDB','origin':'ACTUAL_FEELM','ratings':[],'onboarding':[],'watch_states':[]}


class InputPacketsTests(unittest.TestCase):
    def test_binary_tag_blocks_raw_even_when_numeric_value_looks_like_star(self):
        spy=Mock();packet=m.BinaryPacket(((1,'LIKE'),),'TMDB','ACTUAL_FEELM',4)
        with self.assertRaises(TypeError):m.call_raw_adapter(packet,spy,'TMDB')
        spy.assert_not_called()

    def test_exact_half_stars_and_typed_delivery(self):
        s=snapshot();s['ratings']=[{'movie_id':1,'rating':.5},{'movie_id':2,'rating':5.}]
        s['watch_states']=[{'movie_id':1,'status':'RATED'},{'movie_id':2,'status':'RATED'}]
        p=m.normalize(s,2);spy=Mock(return_value='synthetic-rank')
        self.assertEqual(m.dispatch(p,spy,None,'TMDB'),'synthetic-rank')
        self.assertEqual(spy.call_args.args[0].observations,((1,.5),(2,5.)))
        for bad in (.6,0,5.5,True,float('nan')):
            s['ratings'][0]['rating']=bad
            with self.assertRaises(ValueError):m.normalize(s,4)

    def test_enum_skip_and_hidden_fields(self):
        for bad in (1,-1,'SKIP','UNKNOWN',None):
            s=snapshot();s['onboarding']=[{'movie_id':1,'response':bad}]
            with self.assertRaises(ValueError):m.normalize(s,4)
        for field in ('hidden_rating','histogram','label','score'):
            s=snapshot();s[field]=[]
            with self.assertRaises(ValueError):m.normalize(s,4)
        p=m.normalize(snapshot(),4);self.assertEqual(p.unique_effective_movies,0)
        self.assertEqual(m.dispatch(p,Mock(),None,'TMDB'),'NO_INPUT')

    def test_duplicate_priority_and_delete_restore(self):
        s=snapshot();s['ratings']=[{'movie_id':1,'rating':4.5}];s['onboarding']=[{'movie_id':1,'response':'DISLIKE'}]
        s['watch_states']=[{'movie_id':1,'status':'RATED'}];p=m.normalize(s,4)
        self.assertEqual(p.unique_effective_movies,1);self.assertEqual(p.binary.observations,());self.assertEqual(p.original_onboarding,((1,'DISLIKE'),))
        s['ratings']=[];s['watch_states'][0]['status']='WATCHED_UNRATED';s['input_version']=5;p=m.normalize(s,4)
        self.assertEqual(p.binary.observations,((1,'DISLIKE'),));self.assertEqual(p.watch_states,((1,'WATCHED_UNRATED'),))
        s['onboarding']*=2
        with self.assertRaises(ValueError):m.normalize(s,4)

    def test_current_snapshot_is_idempotent_and_stale_snapshot_rejected(self):
        s=snapshot();s['onboarding']=[{'movie_id':1,'response':'LIKE'}]
        self.assertEqual(m.normalize(s,1),m.normalize(copy.deepcopy(s),4))
        with self.assertRaises(ValueError):m.normalize(s,5)

    def test_mixed_and_unsupported_binary_never_auto_blend(self):
        s=snapshot();s['onboarding']=[{'movie_id':1,'response':'LIKE'}];p=m.normalize(s,4);raw=Mock();binary=Mock(return_value='synthetic-binary')
        self.assertEqual(m.dispatch(p,raw,None,'TMDB'),'UNRESOLVED_BINARY_RANKING');raw.assert_not_called()
        self.assertEqual(m.dispatch(p,raw,binary,'TMDB'),'synthetic-binary');self.assertIs(type(binary.call_args.args[0]),m.BinaryPacket)
        s['ratings']=[{'movie_id':2,'rating':4.}];s['watch_states']=[{'movie_id':2,'status':'RATED'}]
        binary.reset_mock();self.assertEqual(m.dispatch(m.normalize(s,4),raw,binary,'TMDB'),'UNRESOLVED_MIXED_RANKING')
        raw.assert_not_called();binary.assert_not_called()

    def test_namespace_origin_and_watch_consistency(self):
        s=snapshot();s['origin']='MOVIELENS_BINARY_PROXY'
        with self.assertRaises(ValueError):m.normalize(s,4)
        s['id_namespace']='MOVIELENS';s['ratings']=[{'movie_id':1,'rating':5.}];s['watch_states']=[{'movie_id':1,'status':'RATED'}]
        with self.assertRaises(ValueError):m.normalize(s,4)
        s=snapshot();s['ratings']=[{'movie_id':1,'rating':5.}]
        with self.assertRaises(ValueError):m.normalize(s,4)
        s=snapshot();s['watch_states']=[{'movie_id':1,'status':'EXPIRED'}]
        self.assertEqual(m.dispatch(m.normalize(s,4),Mock(),None,'TMDB'),'EXPIRED_POLICY_UNRESOLVED')
        with self.assertRaises(ValueError):m.dispatch(m.normalize(snapshot(),4),Mock(),None,'MOVIELENS')

    def test_direct_dataclass_construction_cannot_bypass_dispatch(self):
        s=snapshot();s['onboarding']=[{'movie_id':1,'response':'LIKE'}]
        p=m.normalize(s,4)
        invalid = [
            replace(p,binary=m.RawRatingPacket(((1,5.),),'TMDB','ACTUAL_FEELM',4)),
            replace(p,binary=replace(p.binary,observations=((1,1),))),
            replace(p,binary=replace(p.binary,version=-1)),
            replace(p,binary=replace(p.binary,version=5)),
            replace(p,binary=replace(p.binary,origin='MOVIELENS_BINARY_PROXY')),
            replace(p,binary=replace(p.binary,observations=[(1,'LIKE')])),
            replace(p,binary=replace(p.binary,observations=([1,'LIKE'],))),
            replace(p,binary=replace(p.binary,observations=((1,'LIKE'),(1,'LIKE')))),
            replace(p,original_onboarding=()),
            replace(p,unique_effective_movies=0),
            replace(p,unique_effective_movies=True),
            replace(p,watch_states=((1,'UNKNOWN'),)),
            replace(p,watch_states=((1,'RATED'),)),
        ]
        s=snapshot();s['ratings']=[{'movie_id':2,'rating':4.5}];s['watch_states']=[{'movie_id':2,'status':'RATED'}]
        rated=m.normalize(s,4)
        invalid += [replace(rated,watch_states=()),
                    replace(rated,binary=replace(rated.binary,observations=((2,'LIKE'),)),original_onboarding=((2,'LIKE'),)),
                    replace(p,binary=replace(p.binary,observations=((1,1),)),watch_states=((3,'EXPIRED'),))]
        for i,bad in enumerate(invalid):
            raw=Mock();binary=Mock()
            with self.subTest(case=i):
                with self.assertRaises((ValueError,TypeError)):m.dispatch(bad,raw,binary,'TMDB')
                raw.assert_not_called();binary.assert_not_called()

    def test_direct_raw_adapter_revalidates_origin_and_immutable_values(self):
        for packet in (m.RawRatingPacket(((1,5.),),'TMDB','MOVIELENS_RAW_PROXY',4),
                       m.RawRatingPacket([(1,5.)],'TMDB','ACTUAL_FEELM',4),
                       m.RawRatingPacket(((1,.6),),'TMDB','ACTUAL_FEELM',4)):
            spy=Mock()
            with self.assertRaises(ValueError):m.call_raw_adapter(packet,spy,'TMDB')
            spy.assert_not_called()


if __name__=='__main__':unittest.main()
