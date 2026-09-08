"""Only synthetic values; no archive, labels, or model payload is read."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rec_ev_032_label_extension as ext


class LabelExtensionTests(unittest.TestCase):
    def setUp(self):
        self.users = {1: "u"}
        self.wanted = {"u": {10, 20}}

    def test_other_user_discards_all_other_fields(self):
        self.assertEqual(ext.read_pair(b"2,PRIVATE_MOVIE,PRIVATE_RATING,PRIVATE_TIME\n",
                                      self.users, self.wanted, 10, 100)[3], "OTHER_USER")

    def test_movie_gate_precedes_rating_and_timestamp(self):
        result = ext.read_pair(b"1,99,DO_NOT_PARSE_RATING,DO_NOT_PARSE_TIME\n",
                               self.users, self.wanted, 10, 100)
        self.assertEqual(result, ("u", 99, None, "OUTSIDE_REQUEST"))

    def test_requested_pair_parses_exact_half_star_but_no_time(self):
        self.assertEqual(ext.read_pair(b"1,10,4.5,INVALID_TIME\n", self.users, self.wanted, 10, 100),
                         ("u", 10, 8, "REQUESTED"))
        for value in [b"3.7", b"nan", b"0"]:
            with self.assertRaises(RuntimeError):
                ext.read_pair(b"1,10," + value + b",NEVER_TIME\n", self.users, self.wanted, 10, 100)

    def test_future_input_removed_from_all_queries_not_recommendations(self):
        rankings = np.arange(1, 17).reshape(4, 2, 2)
        original = rankings.copy()
        requested = ext.requests_for(rankings, {1, 5, 9, 16})
        self.assertEqual(requested, set(range(1, 17)) - {1, 5, 9, 16})
        np.testing.assert_array_equal(rankings, original)
        status = ext.category(5, {5}, {}, {}, "u")
        self.assertEqual(status, "INPUT_RESERVED")
        self.assertEqual(ext.read_pair(b"1,5,PRIVATE_FUTURE_INPUT,TIME\n", self.users,
                                     {"u": requested}, 10, 100)[3], "OUTSIDE_REQUEST")

    def test_q_is_fixed_by_same_H_and_duplicate_pair_is_fatal(self):
        hist = np.array([0, 1, 0, 1, 0, 0, 0, 1, 0, 1])
        found = {}
        ext.add_label(found, "u", 10, 7, hist, {})
        self.assertEqual(found[("u", 10)], (4., .625))
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            ext.add_label(found, "u", 10, 7, hist, {})

    def test_old_labels_crosscheck_raw_and_Q(self):
        hist = np.ones(10, dtype=int)
        old = {("u", 10): (4., .75)}
        found = {}
        ext.add_label(found, "u", 10, 7, hist, old)
        ext.validate_old_found(old, found)
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            ext.add_label({}, "u", 10, 8, hist, old)
        with self.assertRaisesRegex(RuntimeError, "missing"):
            ext.validate_old_found(old, {})

    def test_missing_is_distinct_from_new_old_and_input_reserved(self):
        old = {("u", 10): (4., .8)}
        found = {**old, ("u", 20): (3., .5)}
        self.assertEqual(ext.category(10, set(), old, found, "u"), "OLD_LABEL")
        self.assertEqual(ext.category(20, set(), old, found, "u"), "NEW_LABEL")
        self.assertEqual(ext.category(30, set(), old, found, "u"), "NO_RATING_IN_ARCHIVE")
        self.assertIsNone(ext.clean(float("nan")))

    def test_new_label_narrows_bounds_without_changing_shared_unknown_cancellation(self):
        before = ext.base.paired_bounds([10, 30], [20, 30], {10: .8})
        after = ext.base.paired_bounds([10, 30], [20, 30], {10: .8, 20: .6})
        np.testing.assert_allclose(after[0], [.1, .1])
        self.assertTrue(np.all(after[:, 0] >= before[:, 0] - 1e-12))
        self.assertTrue(np.all(after[:, 1] <= before[:, 1] + 1e-12))

    def test_stale_extension_review_blocks(self):
        with patch.object(ext.base, "check_review"):
            with patch.object(ext, "fingerprint", return_value={"runner": "new"}):
                with patch.object(ext.base, "read_json", return_value={"status": "PASS", "fingerprint": {"runner": "old"}}):
                    with self.assertRaisesRegex(RuntimeError, "stale"):
                        ext.check_review()

    def test_changed_original_source_is_blocked_before_payload_read(self):
        run = object.__new__(ext.Run)
        run.cfg = {"inputs": {"rankings": {"path": "SYNTHETIC", "bytes": 10, "sha256": "sealed"}}}
        with patch.object(run, "guard"):
            with patch.object(ext.base, "pin", return_value={"bytes": 10, "sha256": "changed"}):
                with self.assertRaisesRegex(RuntimeError, "source drift"):
                    run.sources()


if __name__ == "__main__":
    unittest.main()

