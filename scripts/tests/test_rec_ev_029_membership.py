from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import preflight_rec_ev_029_membership as subject  # noqa: E402


def test_split_bucket_matches_contract_formula() -> None:
    salt = "rec-ev-029-direct-user-split-v1"
    for user_id in (1, 42, 1000, 200_948):
        expected = int.from_bytes(
            hashlib.sha256(f"{salt}|{user_id}".encode("utf-8")).digest(), "big"
        ) % 10_000
        assert subject.split_bucket(user_id, salt) == expected


def test_target_counts_cover_only_evenly_named_random_folds() -> None:
    counts = {
        slug: 20 if slug.startswith("R") and slug != "RECENT" else 4
        for slug, _, _ in subject.OUTERS
    }
    assert counts == {
        "R0": 20,
        "R1": 20,
        "R2": 20,
        "R3": 20,
        "R4": 20,
        "KR": 4,
        "RECENT": 4,
    }


def test_scan_membership_reads_only_user_and_movie_fields(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "ratings.zip"
    import zipfile

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
    monkeypatch.setattr(subject, "phase_bucket", lambda _uid: 0)
    monkeypatch.setattr(
        subject,
        "split_bucket",
        lambda uid, _salt: 8500 if uid == 1 else 9500,
    )

    histories, counters = subject.scan_membership(
        {"inputs": {"movielens_archive": {}}, "population": {"split_salt": "x"}},
        {10, 11},
    )

    assert histories == {"SELECTION": {1: [10]}, "REPLICATION": {2: [11]}}
    assert counters["rating_values_parsed"] == 0
    assert counters["timestamps_parsed"] == 0
    assert counters["rec_ev_028_attribution_or_future_movie_membership_opened"] == 0


def test_retained_profiles_are_disjoint_from_every_outer_target(monkeypatch) -> None:
    contract = {
        "inputs": {"parent_contract": {}},
        "population": {"split_salt": "x"},
        "membership": {
            "target_assignment_salt": "target",
            "target_order_salt": "order",
            "profile_order_salt": "profile",
        },
        "coverage_gates": {
            "minimum_users": {"EACH_RANDOM_FOLD": 0, "KR": 0, "RECENT": 0},
            "minimum_unique_target_items": {"EACH_RANDOM_FOLD": 0, "KR": 0, "RECENT": 0},
            "minimum_effective_target_items": {"EACH_RANDOM_FOLD": 0, "KR": 0, "RECENT": 0},
        },
    }
    tmp_parent = ROOT / "docs/recommendation/contracts/rec-ev-027-strict-item-cold-model-screen.json"
    monkeypatch.setattr(subject, "verify", lambda _spec: tmp_parent)
    monkeypatch.setattr(subject, "common_sets", lambda _parent: (set(range(1, 201)), {181, 182, 183, 184}, {185, 186, 187, 188}, set(range(1, 181))))
    histories = {
        cohort: {
            uid: list(range(1, 181)) + [181, 182, 183, 184, 185, 186, 187, 188]
            for uid in range(offset, offset + 2)
        }
        for cohort, offset in (("SELECTION", 1), ("REPLICATION", 101))
    }
    monkeypatch.setattr(subject, "scan_membership", lambda _contract, _universe: (histories, {"rating_values_parsed": 0, "timestamps_parsed": 0}))
    monkeypatch.setattr(subject, "old_user_bucket", lambda _uid: 0)
    monkeypatch.setattr(subject, "user_role_bucket", lambda _uid: 0)
    monkeypatch.setattr(subject, "phase_bucket", lambda _uid: 0)

    def fake_outer(movies, *, slug, **_kwargs):
        if slug.startswith("R") and slug != "RECENT":
            start = 1 + int(slug[1:]) * 20
            cold = list(range(start, start + 20))
        elif slug == "KR":
            cold = [181, 182, 183, 184]
        else:
            cold = [185, 186, 187, 188]
        return [movie for movie in movies if movie not in cold], cold

    monkeypatch.setattr(subject, "outer_pools", fake_outer)

    frame, summary = subject.build_membership(contract)
    assert summary["global_profile_target_intersection_pairs"] == 0
    for cohort in ("SELECTION", "REPLICATION"):
        for user_key, rows in frame.loc[frame["cohort"].eq(cohort)].groupby("user_key"):
            all_targets = {movie for values in rows["target_movie_ids"] for movie in values}
            for profile in rows["profile_movie_ids"]:
                assert not (set(profile) & all_targets), user_key
