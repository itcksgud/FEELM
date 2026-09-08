"""Synthetic adversarial checks; no research payload or Spark is opened."""
import itertools
import sys
import threading
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rec_ev_032_basic as core


class BasicContractTests(unittest.TestCase):
    def test_half_star_rejects_non_grid_and_nonfinite(self):
        self.assertEqual([core.rating_index(v) for v in [.5, 3., 5.]], [0, 5, 9])
        for value in [0, 3.7, 5.5, float("nan"), float("inf")]:
            with self.assertRaises(RuntimeError):
                core.rating_index(value)

    def test_filter_discards_protected_values_before_parsing(self):
        allowed = np.array([False, True, False])
        self.assertIsNone(core.filtered_rating(b"2,PROTECTED,NOT_A_RATING,SECRET_TIME\n", allowed, 20))
        self.assertEqual(core.filtered_rating(b"1,4,4.5,NOT_PARSED\n", allowed, 20), (1, 4, 8))
        with self.assertRaises(RuntimeError):
            core.filtered_rating(b"1,4,3.7,NOT_PARSED\n", allowed, 20)

    def test_training_prior_weights_users_equally(self):
        hist = np.zeros((3, 10)); hist[0, 9] = 100; hist[1, 0] = 1
        prior = core.prior_from_hist(hist)
        self.assertAlmostEqual(prior[0], .25)
        self.assertAlmostEqual(prior[9], .75)

    def test_input_update_recomputes_existing_relative_value(self):
        prior = np.arange(.05, 1., .1)
        old = core.weights([9], prior)
        new = core.weights([9, 1], prior)
        self.assertAlmostEqual(old[0], .75)
        self.assertAlmostEqual(new[0], 2 * .8928571428571429 - 1)
        self.assertNotEqual(old[0], new[0])

    def test_raw_fold_in_count_scaled_regularizer_and_empty(self):
        actual = core.fold_in(np.array([[1.], [2.]]), [1., 5.], .1)
        self.assertAlmostEqual(actual[0], 11 / 5.2)
        self.assertIsNone(core.fold_in(np.empty((0, 1)), [], .1))
        with self.assertRaises(RuntimeError):
            core.fold_in(np.array([[1.]]), [-.5], .1)

    def test_partial_rrf_initializes_missing_contributions(self):
        score = core.rrf([np.array([3, 0])], 6)
        self.assertTrue(np.array_equal(score[[1, 2, 4, 5]], np.zeros(4)))
        self.assertAlmostEqual(score[3], 1 / 61)
        self.assertAlmostEqual(score[0], 1 / 62)
        with self.assertRaises(RuntimeError):
            core.rrf([np.array([2, 2])], 6)

    def test_rank_full_catalog_int64_seen_and_ties(self):
        ids = np.arange(100000, 185517)
        allowed = np.ones(len(ids), dtype=bool); allowed[0] = False
        ranking = core.order(np.zeros(len(ids)), ids, allowed)
        self.assertEqual(ranking.dtype, np.dtype("int64"))
        self.assertEqual(ranking[0], 1)
        self.assertEqual(ranking[-1], 85516)

    def test_supply_missing_component_and_popularity_topup(self):
        ids = np.array([10, 20, 30, 40])
        pop = np.array([2, 0, 3])
        chosen, _, reason = core.choose_hybrid([], pop, ids)
        self.assertEqual(chosen.tolist(), [2, 0]); self.assertEqual(reason, "P0_NO_COMPONENT")
        chosen, scores, reason = core.choose_hybrid([np.array([3])], pop, ids)
        self.assertEqual(chosen.tolist(), [3, 2]); self.assertEqual(reason, "P0_SUPPLY_TOPUP")
        self.assertEqual(scores[1], 0.)

    def test_fixed_reference_and_unknown_bounds(self):
        self.assertAlmostEqual(core.q_from_hist(7, [0, 1, 0, 1, 0, 0, 0, 1, 0, 1]), .625)
        actual = core.metric_bounds([.8, None])
        np.testing.assert_allclose(actual, [[.4, .9], [0, .8], [0, 1]])
        np.testing.assert_allclose(core.metric_bounds([.1, None])[2], [1, 1])
        np.testing.assert_allclose(core.metric_bounds([None, None]), [[0, 1]] * 3)

    def test_unknown_common_item_cancels_in_mean_difference(self):
        np.testing.assert_allclose(core.paired_bounds([1, 9], [2, 9], {1: .8, 2: .6})[0], [.1, .1])
        np.testing.assert_array_equal(core.paired_bounds([1, 9], [9, 1], {}), np.zeros((3, 2)))

    def test_reported_bounds_cover_every_extreme_unknown_completion(self):
        m, p = [1, 3], [2, 3]
        known = {1: .7}
        bounds = core.paired_bounds(m, p, known)
        for q2, q3 in itertools.product([0., .2, .5, 1.], repeat=2):
            labels = {1: .7, 2: q2, 3: q3}
            complete = core.metric_bounds([labels[i] for i in m])[:, 0] - core.metric_bounds([labels[i] for i in p])[:, 0]
            self.assertTrue(np.all(complete >= bounds[:, 0] - 1e-12))
            self.assertTrue(np.all(complete <= bounds[:, 1] + 1e-12))

    def test_shared_bootstrap_preserves_exact_cross_cell_relationship(self):
        values = np.zeros((4, 3, 3, 2))
        for user, v in enumerate([-.2, 0., .1, .4]):
            values[user] = v
        intervals = core.ci_bounds(values, 100, 42, .05, 9)
        np.testing.assert_array_equal(intervals, np.broadcast_to(intervals[0, 0], intervals.shape))

    def test_label_guard_runs_before_any_file_access(self):
        run = object.__new__(core.Run)
        with patch.object(core, "pin", side_effect=AssertionError("should not read")):
            with self.assertRaisesRegex(RuntimeError, "label access"):
                run.source("labels", "prepare")

    def test_absent_or_stale_review_fails_closed(self):
        with patch.object(core, "read_json", return_value={"status": "PASS", "fingerprint": {"runner": "old"}}):
            with patch.object(core, "fingerprint", return_value={"runner": "new"}):
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    core.check_review()

    def test_tampered_score_artifact_fails_before_label_access(self):
        run = object.__new__(core.Run); run.identity = {"runner": "fixed"}; run.root = Path("SYNTHETIC")
        with patch.object(run, "guard"):
            with patch.object(core, "read_json", return_value={"status": "COMPLETE", "fingerprint": run.identity,
                                                             "outputs": {"rankings.npz": {"sha256": "sealed"}}}):
                with patch.object(core, "pin", return_value={"sha256": "tampered"}):
                    with self.assertRaisesRegex(RuntimeError, "modified"):
                        run.validate("score")

    def test_resource_failure_blocks_resume_even_with_stale_budget(self):
        cfg = {"max_seconds": 1800, "max_process_tree_bytes": 12000}
        with self.assertRaisesRegex(RuntimeError, "cannot resume"):
            core.check_remaining_budget({"seconds": 12}, {"status": "RESOURCE_LIMIT", "seconds": 1801}, cfg)
        for previous in [{"seconds": 1800}, {"seconds": 12, "peak_tree_rss_bytes": 12001}]:
            with self.assertRaisesRegex(RuntimeError, "exhausted"):
                core.check_remaining_budget(previous, {}, cfg)
        core.check_remaining_budget({"seconds": 12}, {}, cfg)

    def test_resource_exit_persists_consumed_budget_before_failure(self):
        run = object.__new__(core.Run)
        run.root = Path("SYNTHETIC"); run.identity = {"runner": "fixed"}
        run.peak = 999; run.budget_lock = threading.Lock()
        writes = []
        with patch.object(run, "elapsed", return_value=1801):
            with patch.object(core, "write_json", side_effect=lambda p, v: writes.append((p.name, v))):
                run.save_budget(resource_failure=999)
        self.assertEqual([name for name, _ in writes], ["budget.json", "failure.json"])
        self.assertTrue(all(value["seconds"] == 1801 for _, value in writes))
        self.assertEqual(writes[0][1]["peak_tree_rss_bytes"], 999)
        self.assertEqual(writes[1][1]["status"], "RESOURCE_LIMIT")

    def test_synchronous_guard_persists_limit_before_watchdog_tick(self):
        run = object.__new__(core.Run)
        run.root = Path("SYNTHETIC"); run.identity = {"runner": "fixed"}
        run.peak = 999; run.budget_lock = threading.Lock()
        run.cfg = {"max_seconds": 1800, "max_process_tree_bytes": 12000}
        writes = {}
        with patch.object(core, "check_review", return_value=run.identity):
            with patch.object(run, "elapsed", return_value=1801):
                with patch.object(core, "write_json", side_effect=lambda p, v: writes.update({p.name: v})):
                    with self.assertRaisesRegex(RuntimeError, "resource limit"):
                        run.guard()
        self.assertEqual(writes["budget.json"]["seconds"], 1801)
        self.assertEqual(writes["failure.json"]["status"], "RESOURCE_LIMIT")
        with self.assertRaises(RuntimeError):
            core.check_remaining_budget(writes["budget.json"], writes["failure.json"], run.cfg)


if __name__ == "__main__":
    unittest.main()
