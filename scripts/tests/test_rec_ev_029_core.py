from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rec_ev_029_core import (  # noqa: E402
    ENCODINGS,
    EVEN_K,
    FAMILIES,
    candidate_grid,
    ranked_order,
    score_profile_grid,
)
import run_rec_ev_029_prelabel as prelabel  # noqa: E402
from run_rec_ev_029_prelabel import ResumeError, assert_profiles_match_membership  # noqa: E402


def test_grid_is_exactly_ninety_unique_policies() -> None:
    grid = candidate_grid()
    assert len(grid) == len(FAMILIES) * len(ENCODINGS) * len(EVEN_K) == 90
    assert len({candidate.candidate_id for candidate in grid}) == 90
    assert EVEN_K == tuple(range(2, 31, 2))


def test_ranked_order_uses_score_before_deterministic_tie() -> None:
    order, active = ranked_order(
        [30, 20, 10],
        [0.5, 0.9, 0.5],
        cohort="SELECTION",
        outer="R0",
        system="E5_DIRECT_OWN",
        encoding="PERCENTILE_MAGNITUDE",
        k=8,
        user_key="u",
    )
    assert active
    assert order[0] == 1
    assert sorted(order.tolist()) == [0, 1, 2]
    again, _ = ranked_order(
        [30, 20, 10], [0.5, 0.9, 0.5], cohort="SELECTION", outer="R0",
        system="E5_DIRECT_OWN", encoding="PERCENTILE_MAGNITUDE", k=8, user_key="u",
    )
    assert np.array_equal(order, again)


def test_profile_grid_keeps_k_as_a_prefix_and_returns_valid_top2() -> None:
    target_n = 4
    structured = np.zeros((target_n, 30), dtype=np.float64)
    e5 = np.zeros((target_n, 30), dtype=np.float64)
    for column in range(30):
        structured[:, column] = [column + 1, 0, 1, 2]
        e5[:, column] = [0, column + 1, 2, 1]
    ratings = np.asarray(([0, 9] * 15), dtype=np.int8)
    prior = np.linspace(0.05, 0.95, 10)
    top2, active = score_profile_grid(
        structured,
        e5,
        ratings,
        prior,
        [1, 2, 3, 4],
        cohort="SELECTION",
        outer="KR",
        side="OWN",
        user_key="u",
    )
    assert top2.shape == (90, 2)
    assert active.shape == (90,)
    assert active.any()
    for values in top2[active]:
        assert len(set(map(int, values))) == 2
        assert bool(((0 <= values) & (values < target_n)).all())


def test_zero_binary_weights_are_inactive() -> None:
    similarity = np.arange(120, dtype=np.float64).reshape(4, 30)
    ratings = np.full(30, 4, dtype=np.int8)
    # A midpoint prior of 0.5 makes every identical rating's smoothed weight 0.
    prior = np.full(10, 0.5, dtype=np.float64)
    _, active = score_profile_grid(
        similarity,
        similarity,
        ratings,
        prior,
        [1, 2, 3, 4],
        cohort="REPLICATION",
        outer="RECENT",
        side="SHUFFLE",
        user_key="u",
    )
    assert not active.any()


def test_prepared_profiles_must_preserve_frozen_membership() -> None:
    import pandas as pd
    import pytest

    membership = pd.DataFrame(
        [{
            "cohort": "SELECTION",
            "outer": "R0",
            "track": "RANDOM_ITEM_COLD",
            "fold_or_domain": "0",
            "user_key": "u",
            "profile_movie_ids": [1, 2],
            "target_movie_ids": [3, 4],
            "donor_user_key": "v",
        }]
    )
    profiles = membership.copy()
    profiles["profile_rating_indices"] = [[0, 9]]
    assert_profiles_match_membership(membership, profiles)
    corrupted = profiles.copy()
    corrupted.at[0, "profile_movie_ids"] = [1, 99]
    with pytest.raises(ResumeError, match="profile_movie_ids"):
        assert_profiles_match_membership(membership, corrupted)


def test_score_cell_resume_is_bound_to_current_prepare_seal(tmp_path, monkeypatch) -> None:
    import pytest

    prepare = tmp_path / "prepare.json"
    bundle = tmp_path / "bundle.npz"
    prepare.write_text("prepare", encoding="utf-8")
    bundle.write_text("bundle", encoding="utf-8")
    monkeypatch.setattr(prelabel, "verify", lambda _spec: bundle.resolve())
    monkeypatch.setattr(
        prelabel,
        "artifact",
        lambda path: {"identity": "current"} if path == prepare else {"identity": "other"},
    )
    valid = {
        "status": "SEALED_ALL_90_OWN_AND_PAIRED_SHUFFLE_TOP2",
        "cohort": "SELECTION",
        "outer": "R0",
        "candidate_count": 90,
        "bundle": {},
        "prepare_seal": {"identity": "current"},
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    prelabel.validate_cell_integrity(
        valid,
        p={"prepare_seal": prepare},
        cohort="SELECTION",
        outer="R0",
        bundle_path=bundle,
    )
    stale = {**valid, "prepare_seal": {"identity": "stale"}}
    with pytest.raises(ResumeError, match="score integrity drift"):
        prelabel.validate_cell_integrity(
            stale,
            p={"prepare_seal": prepare},
            cohort="SELECTION",
            outer="R0",
            bundle_path=bundle,
        )
