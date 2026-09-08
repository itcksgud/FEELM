"""Synthetic checks only: no research payload."""
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import numpy as np
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rec_ev_032_conditional as c


class ConditionalTests(unittest.TestCase):
    def test_full_rrf_then_project_differs_from_rrf_in_E(self):
        ids = np.arange(10, 70, 10)
        components = [np.array([0, 2, 3, 4, 1, 5]), np.array([1, 2, 3, 4, 5, 0])]
        scores = c.base.rrf(components, 6)
        full = c.base.order(scores, ids)
        selected, ranks = c.projected_order(full, np.array([0, 1]), 6)
        np.testing.assert_array_equal(selected, [1, 0])
        local = c.base.order(c.base.rrf([np.array([0, 1]), np.array([1, 0])], 2), ids[:2])
        np.testing.assert_array_equal(local, [0, 1])
        self.assertGreater(ranks[0], ranks[1])

    def test_projected_supply_does_not_topup_or_change_E(self):
        target = np.array([1, 3])
        selected, ranks = c.projected_order(np.array([1, 0, 2]), target, 4)
        np.testing.assert_array_equal(target, [1, 3])
        np.testing.assert_array_equal(selected, [0])
        np.testing.assert_array_equal(ranks, [1, 0])
        with self.assertRaises(RuntimeError):
            c.check_supply(np.array([2, 2, len(selected), 2]))

    def test_global_topup_keeps_only_original_topup(self):
        pop = np.array([0, 1, 2, 3])
        score = np.array([0., 0., .1, 0.])
        actual = c.original_order(pop, score, np.array([2, 0]), "P0_SUPPLY_TOPUP", np.arange(4), np.ones(4, bool))
        np.testing.assert_array_equal(actual, [2, 0])
        selected, _ = c.projected_order(actual, np.array([1, 3]), 4)
        self.assertEqual(len(selected), 0)

    def test_no_component_and_no_input_use_pop_order(self):
        for reason in ("P0_NO_INPUT", "P0_NO_COMPONENT"):
            pop = np.array([3, 1, 2, 0])
            np.testing.assert_array_equal(c.original_order(pop, np.zeros(4), pop[:2], reason,
                np.arange(4), np.ones(4, bool)), pop)

    def test_original_top2_mismatch_and_unknown_fallback_fail(self):
        with self.assertRaises(RuntimeError):
            c.original_order(np.arange(3), np.array([1., 2., 3.]), np.array([0, 1]), "NONE",
                np.arange(3), np.ones(3, bool))
        with self.assertRaises(RuntimeError):
            c.original_order(np.arange(3), np.ones(3), np.arange(2), "E_TOPUP",
                np.arange(3), np.ones(3, bool))

    def small_score(self, n, indices=None, has=None, prior=None):
        ids = np.arange(1, 6)
        return c.score_one(ids, np.array([1., 2., 3., 4., 5.]),
            np.arange(.05, 1., .1) if prior is None else prior,
            sparse.csr_matrix(np.eye(5)), np.ones((5, 2)),
            np.ones(5, bool) if has is None else has,
            np.array([1, 2]), np.array([8, 2]) if indices is None else indices, n, .1)

    def test_n0_identity_and_no_label_argument(self):
        orders, scores, diag = self.small_score(0)
        np.testing.assert_array_equal(orders[0], orders[1])
        np.testing.assert_array_equal(scores[0], scores[1])
        self.assertEqual(diag["fallback"], "P0_NO_INPUT")
        for forbidden in ("target", "E", "q", "histogram"):
            self.assertNotIn(forbidden, inspect.signature(c.score_one).parameters)

    def test_profile_excluded_from_both_orders(self):
        orders, scores, diag = self.small_score(2)
        for order in orders:
            self.assertFalse({0, 1}.intersection(order))
        self.assertTrue(diag["als_active"] and diag["content_active"])
        self.assertTrue(np.isneginf(scores[1][:2]).all())

    def test_zero_components_keep_zero_score_digest_and_pop_order(self):
        orders, scores, diag = self.small_score(2, indices=np.array([4, 4]),
            has=np.zeros(5, bool), prior=np.full(10, .5))
        self.assertEqual(diag["fallback"], "P0_NO_COMPONENT")
        np.testing.assert_array_equal(orders[0], orders[1])
        np.testing.assert_array_equal(scores[1][2:], np.zeros(3))

    def test_both_original_digest_and_top2_required(self):
        c.verify_original_scores(["a"], ["a"], np.array([1, 2]), np.array([1, 2]))
        for digest, top in [(["b"], [1, 2]), (["a"], [2, 1])]:
            with self.assertRaises(RuntimeError):
                c.verify_original_scores(digest, ["a"], np.array(top), np.array([1, 2]))

    def test_metric_direction_keeps_risk_sign(self):
        for metric in c.METRICS:
            self.assertEqual(c.direction(metric, -.1, .1), "UNDECIDED")
            self.assertEqual(c.direction(metric, .01, .1),
                "WORSE_DIRECTION" if metric == "HARM20" else "BETTER_DIRECTION")
            self.assertEqual(c.direction(metric, -.1, -.01),
                "BETTER_DIRECTION" if metric == "HARM20" else "WORSE_DIRECTION")

    def test_equal_Q_multisets_have_zero_difference_and_CI(self):
        hist = np.ones(10, dtype=np.int64)
        delta = c.observed_difference([.5, 1.], [1., .5], hist)
        np.testing.assert_array_equal(delta, np.zeros((3, 2)))
        values = np.broadcast_to(delta, (100, 3, 3, 2)).copy()
        ci = c.base.ci_bounds(values, 5000, 20260907, .05, 9)
        np.testing.assert_array_equal(ci, np.zeros((3, 3, 2)))
        for j, metric in enumerate(c.METRICS):
            self.assertEqual(c.direction(metric, *ci[0, j]), "UNDECIDED")

    def test_distinct_Q_multisets_with_same_mean_remain_exactly_tied(self):
        hist = np.ones(10, dtype=np.int64)
        delta = c.observed_difference([.5, 2.], [1., 1.5], hist)
        # Q .05+.35 equals .15+.25, including when float sums would differ.
        np.testing.assert_array_equal(delta[0], [0., 0.])
        self.assertLess(delta[1, 0], 0)

    def test_integer_metrics_match_fixed_Q_and_exact_harm_cutoff(self):
        for hist in (np.ones(10, dtype=np.int64), np.array([2, 0, 3, 4, 1, 1, 3, 2, 1, 3])):
            for raw in ([.5, 5.], [2.5, 3.], [1., 1.]):
                num, den = c.metric_parts(raw, hist)
                q = [c.base.q_from_hist(c.base.rating_index(r), hist) for r in raw]
                np.testing.assert_allclose(num / den, c.base.metric_bounds(q)[:, 0], rtol=0, atol=1e-15)

    def test_review_stale_fail_closed(self):
        with patch.object(c.base, "check_review", return_value={}), patch.object(c, "fingerprint", return_value={"x": "new"}), \
             patch.object(c.base, "read_json", return_value={"status": "PASS", "fingerprint": {"x": "old"}}):
            with self.assertRaises(RuntimeError):
                c.check_review()

    def test_source_hash_failure_precedes_payload(self):
        run = c.Run.__new__(c.Run)
        run.guard = lambda: None
        run.cfg = {"inputs": {"prepared": {"path": "irrelevant", "bytes": 1, "sha256": "old"}}}
        run.paths = {}
        with patch.object(c.base, "pin", return_value={"bytes": 1, "sha256": "new"}), \
             patch.object(c.pd, "read_parquet") as reader:
            with self.assertRaises(RuntimeError):
                run.sources()
            reader.assert_not_called()

    def test_labels_cannot_open_without_score_seal(self):
        run = c.Run.__new__(c.Run)
        run.guard = lambda: None
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.pd, "read_parquet") as reader:
            run.root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                run.load_labels()
            reader.assert_not_called()

    def test_partial_run_preserved_before_any_budget_or_payload(self):
        run = c.Run.__new__(c.Run)
        run.sources = lambda: None
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.pd, "read_parquet") as reader:
            run.root = Path(tmp)
            p = run.root / "failure.json"
            p.write_text("original")
            with self.assertRaises(RuntimeError):
                run.run()
            self.assertEqual(p.read_text(), "original")
            self.assertEqual(len(list(run.root.iterdir())), 1)
            reader.assert_not_called()

    def completion_fixture(self, tmp):
        run = c.Run.__new__(c.Run)
        run.sources = lambda: None
        run.identity = {"test": "identity"}
        run.score = Mock(side_effect=AssertionError("must not rescore"))
        run.validate_score = Mock()
        run.root = Path(tmp)
        for name in c.OUTPUTS:
            (run.root / name).write_bytes(name.encode())
        run.paths = {"score_seal": run.root / "score-seal.json", "evaluate_seal": run.root / "request-seal.json"}
        seal = {"status": "COMPLETE", "fingerprint": run.identity,
            "original_score_seal": c.base.pin(run.paths["score_seal"]),
            "original_evaluate_seal": c.base.pin(run.paths["evaluate_seal"]),
            "outputs": {name: c.base.pin(run.root / name) for name in c.OUTPUTS}}
        c.base.write_json(run.root / "completion-seal.json", seal)
        return run, seal

    def test_completed_run_does_not_rescore(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = self.completion_fixture(tmp)
            run.run()
            run.score.assert_not_called()
            run.validate_score.assert_called_once()

    def test_completion_missing_key_file_or_dependency_is_rejected(self):
        for defect in ("key", "file", "dependency", "empty_outputs"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                run, seal = self.completion_fixture(tmp)
                if defect == "key":
                    del seal["outputs"]["histograms.npz"]
                elif defect == "file":
                    (run.root / "histograms.npz").unlink()
                elif defect == "dependency":
                    seal["original_score_seal"]["sha256"] = "invalid"
                else:
                    seal["outputs"] = {}
                c.base.write_json(run.root / "completion-seal.json", seal)
                with self.assertRaises((RuntimeError, FileNotFoundError)):
                    run.run()
                run.score.assert_not_called()

    def test_resource_time_and_memory_guard_and_watchdog_persist(self):
        for time_over in (False, True):
            for watcher in (False, True):
                with self.subTest(time_over=time_over, watcher=watcher), tempfile.TemporaryDirectory() as tmp:
                    cfg = {"max_seconds": 0 if time_over else 1e6,
                           "max_process_tree_bytes": 10**12 if time_over else 0}
                    b = c.Budget(Path(tmp), {"test": "identity"}, cfg)
                    b.start -= 1.0  # Deterministic elapsed budget despite Windows clock granularity.
                    if watcher:
                        b.done.wait = Mock(return_value=False)
                        with patch.object(c.os, "_exit", side_effect=RuntimeError("exit")):
                            with self.assertRaises(RuntimeError):
                                b.watchdog()
                    else:
                        with self.assertRaises(RuntimeError):
                            b.guard()
                    data = json.loads((Path(tmp) / "failure.json").read_text())
                    self.assertEqual(data["status"], "RESOURCE_LIMIT")
                    self.assertGreater(data["seconds"], 0)
                    self.assertGreater(data["peak_rss_bytes"], 0)
                    self.assertTrue((Path(tmp) / "budget.json").exists())


if __name__ == "__main__":
    unittest.main()
