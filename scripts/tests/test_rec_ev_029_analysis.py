from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rec_ev_029_analysis import (  # noqa: E402
    benefit,
    candidate_is_eligible,
    equal_movie_mean,
    item_contribution_arrays,
    select_winner,
    user_metric_arrays,
)


def test_inactive_policy_uses_analytic_random_for_user_and_item_metrics() -> None:
    q = np.asarray([[0.1, 0.4, 0.8, 0.9]], dtype=np.float64)
    top2 = np.asarray([[-1, -1]], dtype=np.int8)
    active = np.asarray([False])
    user, random_user = user_metric_arrays(q, top2, active)
    item, random_item = item_contribution_arrays(q, top2, active)
    for key in user:
        assert np.array_equal(user[key], random_user[key])
    for key in item:
        assert np.array_equal(item[key], random_item[key])


def test_active_top2_and_item_scaling_match_contract() -> None:
    q = np.asarray([[0.1, 0.4, 0.8, 0.9]], dtype=np.float64)
    top2 = np.asarray([[2, 3]], dtype=np.int8)
    active = np.asarray([True])
    user, _ = user_metric_arrays(q, top2, active)
    item, _ = item_contribution_arrays(q, top2, active)
    assert user["HARM20"].tolist() == [0.0]
    assert user["TOP2_MIN_Q"].tolist() == [0.8]
    assert np.allclose(user["TOP2_MEAN_Q"], [0.85])
    assert np.allclose(item["ITEM_UTILITY"], [[0.0, 0.0, 1.6, 1.8]])
    assert np.array_equal(item["ITEM_LOW"], [[0.0, 0.0, 0.0, 0.0]])


def test_equal_movie_mean_does_not_weight_frequent_movie_more() -> None:
    movies = np.asarray([1, 1, 1, 2])
    values = np.asarray([0.0, 0.0, 0.0, 1.0])
    assert equal_movie_mean(movies, values) == 0.5


def eligible_summary(candidate_id: str, k: int, pooled_harm: float) -> dict:
    family, encoding, _ = candidate_id.split("|")
    return {
        "candidate_id": candidate_id,
        "family": family,
        "encoding": encoding,
        "k": k,
        "minimum_fold_own_active_rate": 1.0,
        "minimum_fold_shuffle_active_rate": 1.0,
        "fold_direction_pass_count": 5,
        "minimum_fold_harm_benefit_own_vs_random": pooled_harm,
        "minimum_fold_harm_benefit_own_vs_shuffle": 0.01,
        "minimum_fold_min_q_benefit_own_vs_random": 0.01,
        "pooled_user_benefits": {
            "OWN_VS_RANDOM": {"HARM20": pooled_harm, "TOP2_MIN_Q": 0.01, "TOP2_MEAN_Q": 0.01},
            "OWN_VS_SHUFFLE": {"HARM20": 0.01, "TOP2_MIN_Q": 0.01, "TOP2_MEAN_Q": 0.01},
        },
        "pooled_item_benefits": {
            "OWN_VS_RANDOM": {"ITEM_UTILITY": 0.01, "ITEM_LOW": 0.0},
            "OWN_VS_SHUFFLE": {"ITEM_UTILITY": 0.01, "ITEM_LOW": 0.0},
        },
        "pooled_user_metrics": {"OWN": {"TOP2_MEAN_Q": 0.6}},
    }


def test_gate_rejects_average_only_candidate_and_key_prefers_harm_benefit() -> None:
    weak = eligible_summary("STRUCTURED_DIRECT|PERCENTILE_MAGNITUDE|K02", 2, 0.01)
    weak["pooled_item_benefits"]["OWN_VS_SHUFFLE"]["ITEM_UTILITY"] = -0.001
    assert not candidate_is_eligible(weak)
    first = eligible_summary("STRUCTURED_DIRECT|PERCENTILE_MAGNITUDE|K02", 2, 0.01)
    second = eligible_summary("E5_DIRECT|BINARY_SIGN|K30", 30, 0.02)
    assert select_winner([first, weak, second])["candidate_id"] == second["candidate_id"]


def test_benefit_orientation_is_risk_first() -> None:
    assert np.isclose(benefit(0.1, 0.2, "HARM20"), 0.1)
    assert np.isclose(benefit(0.7, 0.6, "TOP2_MIN_Q"), 0.1)
    assert np.isclose(benefit(0.1, 0.2, "ITEM_LOW"), 0.1)
