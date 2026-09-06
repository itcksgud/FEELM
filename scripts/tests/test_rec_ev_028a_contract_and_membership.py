from __future__ import annotations

import json
from pathlib import Path
import tempfile
import zipfile

from scripts import preflight_rec_ev_028a_membership as membership
from scripts import preflight_rec_ev_028a1_membership as balanced_membership
from scripts import validate_rec_ev_028a_contract as validator
from scripts import validate_rec_ev_028a1_amendment as amendment_validator
from scripts import validate_rec_ev_028a2_amendment as a2_validator
from scripts.rec_ev_022a_core import old_user_bucket, user_role_bucket


def test_exact_contract_pin_and_parent_pin() -> None:
    contract = json.loads(validator.DEFAULT.read_text(encoding="utf-8"))
    validator.validate(contract)


def test_exact_amendment_pin_and_trigger_pins() -> None:
    amendment = json.loads(amendment_validator.DEFAULT.read_text(encoding="utf-8"))
    amendment_validator.validate(amendment)


def test_exact_a2_amendment_and_transition_pins() -> None:
    amendment = json.loads(a2_validator.DEFAULT.read_text(encoding="utf-8"))
    a2_validator.validate(amendment)


def test_phase_bucket_is_stable_bounded_and_salted() -> None:
    assert membership.phase_bucket(7) == membership.phase_bucket(7)
    assert 0 <= membership.phase_bucket(7) < 10_000
    assert membership.phase_bucket(7) != membership.phase_bucket(7, "other")


def _eligible_uid_in_phase(low: int, high: int, maximum: int = 100_000) -> int:
    return next(
        uid for uid in range(1, maximum)
        if old_user_bucket(uid) <= 59
        and user_role_bucket(uid) <= 5999
        and low <= membership.phase_bucket(uid) <= high
    )


def test_reader_never_parses_fit_future_or_rating_fields() -> None:
    fit_uid = _eligible_uid_in_phase(0, 7999)
    attribution_uid = _eligible_uid_in_phase(8000, 8999)
    future_uid = _eligible_uid_in_phase(9000, 9999)
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "ratings.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(
                "ml/ratings.csv",
                "userId,movieId,rating,timestamp\n"
                f"{fit_uid},10,NOT_A_RATING,NOT_A_TIMESTAMP\n"
                f"{attribution_uid},11,NOT_A_RATING,NOT_A_TIMESTAMP\n"
                f"{future_uid},12,NOT_A_RATING,NOT_A_TIMESTAMP\n",
            )
        histories, counters = membership.scan_attribution_membership(
            archive, {10, 11, 12}, max_user_id=max(fit_uid, attribution_uid, future_uid)
        )
    assert histories == {attribution_uid: [11]}
    assert counters["rating_values_parsed"] == 0
    assert counters["timestamps_parsed"] == 0
    assert counters["future_rows_discarded_after_user_id_only"] == 1


def test_effective_items_detects_concentration() -> None:
    assert membership.effective_items([[1], [2], [3], [4]]) == 4.0
    assert membership.effective_items([[1], [1], [1], [2]]) < 2.0


def test_balanced_allocator_prefers_low_load_then_low_availability() -> None:
    rows = [
        {"user_key": "a", "cold_movie_ids": [1, 2, 3]},
        {"user_key": "b", "cold_movie_ids": [1, 2]},
        {"user_key": "c", "cold_movie_ids": [1, 3]},
    ]
    selected = balanced_membership.balanced_targets(
        rows,
        track="TEST",
        fold="0",
        target_n=1,
        salt="rec-ev-028a1-balanced-target-v1",
        target_order_salt="target",
    )
    assert set(selected) == {"a", "b", "c"}
    assert all(len(value) == 1 for value in selected.values())
    assert len({value[0] for value in selected.values()}) >= 2


def test_balanced_allocator_is_input_order_invariant() -> None:
    rows = [
        {"user_key": "b", "cold_movie_ids": [1, 2, 4]},
        {"user_key": "a", "cold_movie_ids": [1, 2, 3]},
    ]
    kwargs = {
        "track": "TEST",
        "fold": "0",
        "target_n": 2,
        "salt": "rec-ev-028a1-balanced-target-v1",
        "target_order_salt": "target",
    }
    assert balanced_membership.balanced_targets(rows, **kwargs) == balanced_membership.balanced_targets(
        list(reversed(rows)), **kwargs
    )


def test_recent_uses_recent_coverage_thresholds_not_random_thresholds() -> None:
    thresholds = {
        "minimum_users": {"EACH_RANDOM_FOLD": 500, "RECENT": 2},
        "minimum_unique_target_items": {"EACH_RANDOM_FOLD": 1000, "RECENT": 2},
        "minimum_effective_target_items": {"EACH_RANDOM_FOLD": 500, "RECENT": 2},
    }
    result = balanced_membership._coverage_truth(
        slug="RECENT", targets=[[1], [2]], users=2, thresholds=thresholds
    )
    assert result["coverage_truth"] == "BROAD_ITEM_ELIGIBLE"
