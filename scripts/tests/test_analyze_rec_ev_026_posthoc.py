from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts import analyze_rec_ev_026_posthoc as subject


def test_equal_count_quartile_is_complete_and_balanced() -> None:
    frame = pd.DataFrame(
        {
            "evidence_id": ["A"] * 7 + ["B"] * 4,
            "user_key022": [f"u{i}" for i in range(7)] + [f"v{i}" for i in range(4)],
            "value": [1.0] * 7 + [1.0, 2.0, 3.0, 4.0],
        }
    )
    quartiles = subject.equal_count_quartile(frame, "value")
    assert set(quartiles.tolist()) == {1, 2, 3, 4}
    assert quartiles.groupby(level=0).size().to_dict() == {"A": 7, "B": 4}


def test_release_bucket_has_locked_recent_year_resolution() -> None:
    assert subject.release_bucket("REC-EV-026B", 2022.0) == "2022"
    assert subject.release_bucket("REC-EV-026A", 2022.0) == "2020_2023"
    assert subject.release_bucket("REC-EV-026A", 2013.0) == "2010_2019"


def test_posthoc_run_is_read_only_and_writes_expected_tables(tmp_path: Path) -> None:
    before = subject.inventory(subject.SEALED)
    summary = subject.run(tmp_path)
    after = subject.inventory(subject.SEALED)
    assert before == after
    assert summary["status"] == "POSTHOC_DESCRIPTIVE_ONLY"
    assert summary["sealed_source"]["unchanged_after_analysis"] is True
    assert summary["precision"]["simultaneous_contrasts"] == 312
    assert summary["precision"]["imprecise_contrasts_half_width_gte_0_05"] == 26
    checks = summary["precision"]["e5_to_bpr_point_estimate_margin_check"]
    assert [(row["experiment"], row["kind"], row["contrasts"]) for row in checks] == [
        ("REC-EV-026A", "ABSOLUTE", 12),
        ("REC-EV-026A", "INCREMENTAL", 24),
        ("REC-EV-026B", "ABSOLUTE", 12),
        ("REC-EV-026B", "INCREMENTAL", 24),
    ]
    for name in summary["tables"].values():
        assert (tmp_path / name).is_file()
    written = json.loads((tmp_path / "posthoc-analysis.json").read_text(encoding="utf-8"))
    assert written == summary
