from __future__ import annotations

import sys
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import preflight_rec_ev_030_membership as subject  # noqa: E402


def test_future_reader_opens_only_user_and_movie_fields(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "ratings.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "ml-32m/ratings.csv",
            "userId,movieId,rating,timestamp\n"
            "1,10,NOT_A_RATING,NOT_A_TIMESTAMP\n"
            "2,11,ALSO_NOT_A_RATING,ALSO_NOT_A_TIMESTAMP\n",
        )
    monkeypatch.setattr(subject, "MAX_USER_ID", 2)
    monkeypatch.setattr(subject, "EXPECTED_RATING_ROWS", 2)
    monkeypatch.setattr(subject, "verify", lambda _spec: archive)
    monkeypatch.setattr(subject, "old_user_bucket", lambda _uid: 0)
    monkeypatch.setattr(subject, "user_role_bucket", lambda _uid: 0)
    monkeypatch.setattr(subject, "phase_bucket", lambda uid: 9500 if uid == 1 else 8500)
    histories, counters = subject.scan_future_membership({"inputs": {"movielens_archive": {}}}, {10, 11})
    assert histories == {1: [10]}
    assert counters["rating_values_parsed"] == 0
    assert counters["timestamps_parsed"] == 0
    assert counters["rec_ev_028_attribution_or_fit_movie_membership_opened"] == 0
