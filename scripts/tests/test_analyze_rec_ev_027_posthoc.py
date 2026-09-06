from __future__ import annotations

import pandas as pd

from scripts import analyze_rec_ev_027_posthoc as subject


def test_fixed_buckets_cover_boundaries() -> None:
    assert [subject.history_bucket(value) for value in (20, 49, 50, 99, 100, 499, 500)] == [
        "20_49", "20_49", "50_99", "50_99", "100_499", "100_499", "500_PLUS"
    ]
    assert [subject.popularity_bucket(value) for value in (1, 9, 10, 49, 50, 199, 200, 999, 1000)] == [
        "1_9", "1_9", "10_49", "10_49", "50_199", "50_199", "200_999", "200_999", "1000_PLUS"
    ]


def test_release_bucket_is_descriptive_not_track_dependent() -> None:
    assert subject.release_bucket(float("nan")) == "UNKNOWN"
    assert subject.release_bucket(1979) == "PRE_1980"
    assert subject.release_bucket(1999) == "1980_1999"
    assert subject.release_bucket(2019) == "2010_2019"
    assert subject.release_bucket(2023) == "2020_2023"
    assert subject.release_bucket(2024) == "2024_PLUS"


def test_equal_count_quartile_balances_with_ties() -> None:
    frame = pd.DataFrame(
        {
            "outer": ["R1"] * 7 + ["R2"] * 4,
            "user_key": [f"u{i}" for i in range(7)] + [f"v{i}" for i in range(4)],
            "signal": [1.0] * 7 + [1.0, 2.0, 3.0, 4.0],
        }
    )
    q = subject.equal_count_quartile(frame, ["outer"], "signal")
    assert set(q.tolist()) == {1, 2, 3, 4}
    assert q.groupby(level=0).size().to_dict() == {"R1": 7, "R2": 4}


def test_user_segment_summary_preserves_direction() -> None:
    paired = pd.DataFrame(
        {
            "outer": ["R1", "R1"],
            "encoding": ["PERCENTILE_MAGNITUDE"] * 2,
            "user_key": ["a", "b"],
            "history_bucket": ["20_49", "20_49"],
            "profile_signal_quartile": ["Q1", "Q2"],
            "profile_positive": [4, 5],
            "lightfm_harm": [0.0, 1.0],
            "direct_harm": [1.0, 1.0],
            "harm_benefit": [1.0, 0.0],
            "mean_q_benefit": [0.2, -0.1],
            "min_q_benefit": [0.1, -0.2],
            "lightfm_beats_direct_harm": [True, False],
            "lightfm_beats_direct_mean_q": [True, False],
            "seed_top2_overlap_lightfm": [1.0, 0.5],
        }
    )
    result = subject.summarize_user_segments(paired)
    row = result.loc[
        result["segment"].eq("history_bucket") & result["segment_value"].eq("20_49")
    ].iloc[0]
    assert row["users"] == 2
    assert row["harm_benefit"] == 0.5
    assert row["mean_q_benefit"] == 0.05
    assert row["mean_seed_top2_overlap"] == 0.75
