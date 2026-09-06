from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import zipfile

from scripts.preflight_rec_ev_027_membership import evaluation_phase_bucket, hash_order, scan_evaluation_membership
from scripts.rec_ev_022a_core import old_user_bucket, user_role_bucket


class RecEv027MembershipTests(unittest.TestCase):
    def test_hash_order_does_not_accept_ratings(self) -> None:
        one = hash_order([1, 2, 3], salt="s", track="t", fold_or_domain="f", key="u")
        two = hash_order([3, 2, 1], salt="s", track="t", fold_or_domain="f", key="u")
        self.assertEqual(one, two)

    def test_phase_bucket_is_stable_and_bounded(self) -> None:
        self.assertEqual(evaluation_phase_bucket(11), evaluation_phase_bucket(11))
        self.assertTrue(all(0 <= evaluation_phase_bucket(uid) < 10_000 for uid in range(1, 100)))
        self.assertNotEqual(evaluation_phase_bucket(11), evaluation_phase_bucket(11, "other"))

    def test_reader_selects_movie_membership_without_parsing_bad_rating(self) -> None:
        eligible_uid = next(
            uid for uid in range(1, 5000)
            if old_user_bucket(uid) <= 59 and 6000 <= user_role_bucket(uid) <= 7999
        )
        excluded_uid = next(
            uid for uid in range(1, 5000)
            if old_user_bucket(uid) > 59 or not 6000 <= user_role_bucket(uid) <= 7999
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(
                    "ml/ratings.csv",
                    "userId,movieId,rating,timestamp\n"
                    f"{eligible_uid},11,NOT_A_RATING,NOT_A_TIMESTAMP\n"
                    f"{excluded_uid},12,NOT_A_RATING,NOT_A_TIMESTAMP\n",
                )
            histories, counters = scan_evaluation_membership(archive, {11, 12}, max_user_id=5000)
        self.assertEqual({eligible_uid: [11]}, histories)
        self.assertEqual(0, counters["rating_values_parsed"])
        self.assertEqual(0, counters["timestamps_parsed"])


if __name__ == "__main__":
    unittest.main()
