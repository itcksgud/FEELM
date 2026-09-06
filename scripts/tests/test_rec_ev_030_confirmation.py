from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_rec_ev_030_confirmation as subject  # noqa: E402


def _row(user: str, own_active: bool, shuffle_active: bool, value: float) -> dict:
    row = {
        "user_key": user,
        "full_history_count": 100,
        "own_active": own_active,
        "shuffle_active": shuffle_active,
    }
    for system in subject.SYSTEMS:
        for metric in subject.USER_METRICS:
            row[f"{system}__{metric}"] = value
    return row


def test_random_pooled_active_is_per_user_all_available_folds() -> None:
    frames = {
        outer: pd.DataFrame([
            _row("a", not (outer == "R1"), True, 1.0),
            _row("b", True, True, 2.0),
        ])
        for outer in subject.RANDOM_OUTERS
    }
    pooled = subject._pooled_users(frames).set_index("user_key")
    assert not bool(pooled.loc["a", "own_active"])
    assert bool(pooled.loc["a", "shuffle_active"])
    assert bool(pooled.loc["b", "own_active"])
    assert pooled.loc["a", "OWN__TOP2_MEAN_Q"] == 1.0


def test_primary_item_transform_has_exact_four_benefit_columns() -> None:
    transform = subject._item_transform()
    assert transform.shape == (6, 4)
    base = np.asarray([0.4, 0.3, 0.2, 0.5, 0.1, 0.6])
    # Base order is OWN utility/low, SHUFFLE utility/low, RANDOM utility/low.
    assert np.allclose(base @ transform, [0.3, 0.3, 0.2, 0.2])


def test_frozen_policy_has_two_contrasts_and_six_user_columns() -> None:
    assert subject.CONTRASTS == (("OWN", "RANDOM"), ("OWN", "SHUFFLE"))
    assert len(subject.CONTRASTS) * len(subject.USER_METRICS) == 6
    assert len(subject.CONTRASTS) * len(subject.ITEM_METRICS) == 4


@pytest.mark.parametrize("artifact_name", ["user_boot", "metric_seal"])
def test_partial_analysis_state_fails_closed_before_resume(tmp_path: Path, artifact_name: str) -> None:
    paths = {
        name: tmp_path / name
        for name in (
            "metric_seal",
            "user_boot",
            "item_boot",
            "inference",
            "descriptive",
            "user_segments",
            "movie_segments",
        )
    }
    paths[artifact_name].write_bytes(b"partial")
    with pytest.raises(subject.ResumeError, match="partial future analysis state"):
        subject.reject_partial_analysis(paths)
