"""Synthetic checks: never open real profiles, targets, or raw ratings."""
import sys
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rec_ev_031_catalog_bridge as m


def test_disallowed_user_rating_and_timestamp_not_parsed():
    lines=[b"1,7,0.5,DO_NOT_PARSE\n",b"2,NOT_A_MOVIE,NOT_A_RATING,NO_TIMESTAMP\n",b"1,7,5.0,ALSO_NOT_TIME\n"]
    count,total,hist,audit=m.scan_calibration(lines,np.array([False,True,False]),10)
    assert count[7]==2 and total[7]==5.5
    assert hist[1,0]==1 and hist[1,9]==1 and hist[2].sum()==0
    assert audit["noncalibration_rows_discarded_after_user_id"]==1
    assert audit["calibration_ratings_parsed"]==2 and audit["timestamps_parsed"]==0


@pytest.mark.parametrize("rating,index",[(.5,0),(1,1),(3.5,6),(4,7),(4.5,8),(5,9)])
def test_exact_half_star_grid(rating,index):
    assert m.rating_index(rating)==index


@pytest.mark.parametrize("rating",[0,5.5,4.25])
def test_invalid_star_does_not_silently_round(rating):
    with pytest.raises(ValueError):m.rating_index(rating)


def test_observed_prefix_weights_match_existing_verified_formula():
    from rec_ev_027_core import profile_weights
    prior=np.arange(.05,1,.1)
    idx=np.array([0,0,4,7,9])
    assert np.allclose(m.profile_weights(idx,prior),profile_weights(idx,prior,"PERCENTILE_MAGNITUDE"))
    assert m.profile_weights(idx,prior)[0]<0<m.profile_weights(idx,prior)[-1]


def test_calibration_roles_match_existing_reader_conditions():
    from rec_ev_022a_core import old_user_bucket,user_role_bucket
    from preflight_rec_ev_029_membership import phase_bucket,split_bucket
    for uid in (1,2,10,150,555,4321,200000,200948):
        expected=(old_user_bucket(uid)<=59 and user_role_bucket(uid)<=5999 and phase_bucket(uid)<=7999
                  and split_bucket(uid,"rec-ev-029-direct-user-split-v1")<=7999)
        assert m.calibration_user(uid)==expected
    assert not m.calibration_user(0) and not m.calibration_user(200949)


def test_sparse_profile_matches_weighted_pairwise_cosine():
    features=sparse.csr_matrix(np.array([[1.,0.],[0.,1.],[2**-.5,2**-.5]]))
    weights=np.array([-.2,.8])
    h=sparse.csr_matrix((weights/abs(weights).sum(),([0,0],[0,1])),shape=(1,3))
    batch=(features@(h@features).T).toarray().ravel()
    direct=(features@features[[0,1]].T).toarray()@weights/abs(weights).sum()
    assert np.allclose(batch,direct)


def test_full_catalog_indices_do_not_overflow_and_seen_only_filters_observed():
    items=np.arange(1,85518,dtype=np.int64)
    scores=np.zeros(len(items));scores[-1]=10;scores[1]=9
    order=m.ranked(scores,items,[1])
    assert order.dtype==np.int64 and order[0]==85516 and 1 not in order
    assert order[1]==0 and len(order)==85516


def test_duplicate_union_fills_budget_deterministically():
    a=np.arange(20);b=np.r_[0,1,2,9,8,7,6,5,4,3,np.arange(10,20)]
    result=m.union_candidates(a,b,6)
    assert result.tolist()==[0,1,2,3,9,4]
    assert len(np.unique(result))==6


def test_rrf_respects_union_and_unseen_mask():
    items=np.arange(1,21);scores=np.arange(20,dtype=float)
    a=m.ranked(scores,items,[19]);b=m.ranked(-scores,items,[19])
    out=m.fused_order(a,b,items,budget=6)
    assert len(out)==6 and 19 not in out
    assert set(out)==set(m.union_candidates(a,b,6))


def test_unknown_is_not_bad_and_binary_gain_idcg_is_explicit():
    top=np.r_[999,10,20,np.arange(1000,1497)]
    r=m.retrieval_metrics(top,[10,20,30],[.9,.1,.85])
    assert r["recall3"]==.5 and r["judged3"]==2 and r["known_bad3"]==1
    assert r["conditional_bad3"]==.5 and r["known_bad_lower_bound3"]==pytest.approx(1/3)
    assert r["ndcg3"]==pytest.approx((1/np.log2(3))/(1+1/np.log2(3)))


def test_empty_judgments_and_no_positives_stay_undefined():
    r=m.retrieval_metrics(np.arange(100,600),[1,2],[.1,.2])
    assert np.isnan(r["recall500"]) and np.isnan(r["ndcg3"])
    assert np.isnan(r["conditional_bad3"]) and r["known_bad_lower_bound3"]==0
    assert r["judged_fraction3"]==0


def test_bootstrap_uses_paired_users_and_excludes_same_missing_denominator():
    result=m.paired_interval([.1,.4,np.nan],[0,.3,np.nan],500,42,4)
    assert result["n"]==2 and result["mean"]==pytest.approx(.1)
    assert result["ci"]==pytest.approx([.1,.1]) and result["confidence"]==.9875


def test_label_source_blocked_before_path_resolution():
    run=m.Run.__new__(m.Run)
    with pytest.raises(RuntimeError,match="before evaluate"):
        run.source("labels","prepare")


def test_invalid_score_seal_prevents_label_access():
    run=m.Run.__new__(m.Run)
    run.validate_scores=Mock(side_effect=RuntimeError("bad seal"))
    run.source=Mock()
    with pytest.raises(RuntimeError,match="bad seal"):run.evaluate()
    run.source.assert_not_called()


def synthetic_run(tmp_path, monkeypatch, previous=0, peak=100):
    run=m.Run.__new__(m.Run)
    run.root=tmp_path
    run.config_path=tmp_path/"config.json"
    run.cfg={}
    run.fingerprint={"code_sha256":"synthetic"}
    run.start=run.phase_start=100.
    run.previous_seconds=previous
    monkeypatch.setattr(m.time,"monotonic",lambda:110.)
    monkeypatch.setattr(m,"resident_bytes",lambda:peak)
    monkeypatch.setattr(m,"check_audit",lambda *args:run.fingerprint.copy())
    return run


@pytest.mark.parametrize("name",["prepare-seal.json","score-seal.json","evaluation-seal.json"])
@pytest.mark.parametrize("previous,peak,reason",[(1791,100,"aggregate"),(0,8*1024**3+1,"memory")])
def test_resource_limit_cannot_publish_any_completion_seal(tmp_path,monkeypatch,name,previous,peak,reason):
    run=synthetic_run(tmp_path,monkeypatch,previous,peak)
    with pytest.raises(RuntimeError,match=reason):run.seal(name,{})
    assert not (tmp_path/name).exists()
    assert m.read_json(tmp_path/"execution-budget.json")["total_execution_seconds"]==previous+10


def test_changed_identity_or_revoked_audit_cannot_publish_or_access_label(tmp_path,monkeypatch):
    run=synthetic_run(tmp_path,monkeypatch)
    monkeypatch.setattr(m,"check_audit",lambda *args:{"code_sha256":"changed"})
    with pytest.raises(RuntimeError,match="identity changed"):run.seal("score-seal.json",{})
    assert not (tmp_path/"score-seal.json").exists()
    monkeypatch.setattr(m,"check_audit",Mock(side_effect=RuntimeError("audit revoked")))
    with pytest.raises(RuntimeError,match="audit revoked"):run.source("labels","evaluate")


def test_phase_completion_uses_aggregate_time_and_resumes_across_processes(tmp_path,monkeypatch):
    run=synthetic_run(tmp_path,monkeypatch,previous=90)
    run.seal("prepare-seal.json",{})
    assert m.read_json(tmp_path/"prepare-seal.json")["seconds"]==10
    assert m.read_json(tmp_path/"prepare-seal.json")["total_execution_seconds"]==100
    cfg=tmp_path/"config.json"
    m.write_json(cfg,{"ks":list(m.KS),"policies":list(m.POLICIES),"candidate_budget":500,"output_root":str(tmp_path)})
    restarted=m.Run(cfg)
    assert restarted.previous_seconds==100


def test_progress_guard_runs_even_for_disallowed_user_rows():
    from itertools import repeat
    callback=Mock()
    m.scan_calibration(repeat(b"1,DO_NOT_PARSE,NO_LABEL,NO_TIME\n",1000000),np.array([False,False]),1,callback)
    callback.assert_called_once_with(1000000,0)
