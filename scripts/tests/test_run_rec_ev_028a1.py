from __future__ import annotations

import numpy as np

from scripts.run_rec_ev_027_screen import fold_in_lightfm
from scripts.run_rec_ev_028a1 import CELLS, SYSTEMS, fold_in_user
from scripts.run_rec_ev_028a2 import load_contracts as load_a2_contracts
from scripts.run_rec_ev_028a2 import ResumeError as A2ResumeError
from scripts.run_rec_ev_028a2 import _strict_int_list
from scripts.run_rec_ev_028a2 import validate_membership_and_summary
from scripts.validate_rec_ev_028a2_amendment import DEFAULT as A2_DEFAULT, ROOT
import pytest


def test_declared_system_and_cell_cartesian_is_frozen() -> None:
    assert [cell for cell, _, _ in CELLS] == [
        "PERCENTILE_MAGNITUDE_K8",
        "BINARY_SIGN_K8",
        "PERCENTILE_MAGNITUDE_K4",
        "PERCENTILE_MAGNITUDE_K12",
    ]
    assert list(SYSTEMS) == [
        "FULL_PERSONALIZED",
        "BIAS_ONLY",
        "DOT_ONLY",
        "PROFILE_SHUFFLE",
        "STRUCTURED_DIRECT",
    ]


def test_fold_in_decomposition_matches_parent_full_score() -> None:
    rng = np.random.default_rng(3)
    biases = rng.normal(size=7)
    factors = rng.normal(size=(7, 5))
    profile = np.asarray([0, 2, 4], dtype=np.int64)
    target = np.asarray([1, 3, 5, 6], dtype=np.int64)
    weights = np.asarray([-0.8, 0.3, 0.9], dtype=np.float64)
    user, active = fold_in_user(
        biases, factors, profile, weights, regularization=1e-6
    )
    parent_scores, parent_active = fold_in_lightfm(
        biases, factors, profile, weights, target, regularization=1e-6
    )
    assert active and parent_active
    np.testing.assert_allclose(biases[target] + factors[target] @ user, parent_scores)


def test_fold_in_zero_weights_is_inactive() -> None:
    user, active = fold_in_user(
        np.zeros(3),
        np.ones((3, 2)),
        np.asarray([0, 1]),
        np.asarray([0.0, 0.0]),
        regularization=1e-6,
    )
    assert not active
    np.testing.assert_array_equal(user, np.zeros(2))


def test_a2_membership_and_summary_are_recursively_valid() -> None:
    amendment, base, _, _ = load_a2_contracts(A2_DEFAULT)
    frame, summary = validate_membership_and_summary(
        amendment,
        base,
        ROOT / amendment["output_root"],
    )
    assert len(frame) == 14_889
    assert summary["global_profile_target_intersection_pairs"] == 0


def test_resume_integer_payload_rejects_coercible_nonintegers() -> None:
    assert _strict_int_list([np.int64(1), 2], "test") == [1, 2]
    with pytest.raises(A2ResumeError):
        _strict_int_list([1.0, 2.0], "test")
    with pytest.raises(A2ResumeError):
        _strict_int_list(["1", "2"], "test")
