"""Synthetic-only tests for hybrid345 evaluation semantics."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import hybrid345_evaluate as ev


class Hybrid345EvaluateTests(unittest.TestCase):
    def test_final344_calibration_reference_is_bound_to_input_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_lock_relative = "locks/input-lock.json"
            input_lock_path = root / input_lock_relative
            ev.write_json(input_lock_path, {"fixed": True})
            calibration_path = root / ev.FINAL344_CALIBRATION_RELATIVE
            calibration_path.parent.mkdir(parents=True)
            ev.write_json(calibration_path, {"scope": "DEVELOPMENT_ONLY", "fits": []})
            seal_path = root / ev.FINAL344_CALIBRATION_SEAL_RELATIVE
            ev.write_json(seal_path, {
                "input_lock": ev.pin(input_lock_path),
                "files": {"final-calibration.json": ev.pin(calibration_path)},
                "execution": {},
            })
            config = {
                "sources": {"final344_input_lock": input_lock_relative},
                "source_pins": {input_lock_relative: ev.pin(input_lock_path)},
            }
            self.assertEqual(ev.verify_final344_calibration_parent(config, root), {
                "final344_calibration_seal": ev.pin(seal_path),
                "final344_calibration": ev.pin(calibration_path),
            })
            with calibration_path.open("ab") as handle:
                handle.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "sealed SHA mismatch"):
                ev.verify_final344_calibration_parent(config, root)

    def test_vote_metadata_is_loaded_through_final344_input_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata_path = root / ev.TMDB_VOTE_METADATA_RELATIVE
            metadata_path.parent.mkdir(parents=True)
            expected = pd.DataFrame({"movie_id": [1, 2], "tmdb_vote_count": [0.0, 17.0]})
            expected.to_parquet(metadata_path, index=False)
            input_lock_relative = "locks/input-lock.json"
            input_lock_path = root / input_lock_relative
            ev.write_json(input_lock_path, {
                "evaluation_scope": "DEVELOPMENT_ONLY",
                "evaluation_labels_read": False,
                "files": {ev.TMDB_VOTE_METADATA_RELATIVE: ev.pin(metadata_path)},
            })
            config = {
                "sources": {"final344_input_lock": input_lock_relative},
                "source_pins": {input_lock_relative: ev.pin(input_lock_path)},
            }
            catalog = pd.DataFrame({"movie_id": [1, 2]})
            actual, inputs = ev.load_tmdb_vote_metadata(config, catalog, root)
            pd.testing.assert_frame_equal(actual, expected)
            self.assertEqual(inputs, {
                "final344_input_lock": ev.pin(input_lock_path),
                "tmdb_vote_metadata": ev.pin(metadata_path),
            })

            with metadata_path.open("ab") as handle:
                handle.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "sealed SHA mismatch"):
                ev.load_tmdb_vote_metadata(config, catalog, root)

    def test_affine_is_user_macro_and_nonnegative(self) -> None:
        frame = pd.DataFrame(
            {
                "uid": [1, 1, 1, 2],
                "movie_id": [10, 11, 12, 20],
                "raw": [1.0, 2.0, 3.0, 10.0],
                "rating": [1.0, 2.0, 3.0, 5.0],
            }
        )
        fit = ev.affine_by_user(frame, min_users=2, min_rows=4)
        weights = np.array([1 / 6, 1 / 6, 1 / 6, 1 / 2])
        x, y = frame.raw.to_numpy(), frame.rating.to_numpy()
        mx, my = weights @ x, weights @ y
        expected_b = max(0.0, float(weights @ ((x - mx) * (y - my)) / (weights @ ((x - mx) ** 2))))
        self.assertAlmostEqual(fit["b"], expected_b)
        self.assertAlmostEqual(fit["a"], my - expected_b * mx)
        reversed_frame = frame.assign(rating=[5.0, 4.0, 3.0, 1.0])
        reversed_fit = ev.affine_by_user(reversed_frame, min_users=2, min_rows=4)
        self.assertEqual(reversed_fit["b"], 0.0)
        self.assertEqual(reversed_fit["state"], "CONSTANT")

    def test_ranking_metrics_and_low_definitions(self) -> None:
        # Equal scores are resolved by movie_id, so IDs 10 then 20 are shown.
        y = np.array([1.0, 5.0, 2.0, 4.0])
        raw = np.array([0.7, 0.7, 0.1, 0.0])
        ids = np.array([20, 10, 30, 40])
        top = ev.top_quality(y, raw, ids, 2)
        self.assertEqual(top["stars"], 3.0)
        self.assertEqual(top["low"], 0.5)
        self.assertEqual(top["any_low"], 1.0)
        self.assertEqual(top["both_low"], 0.0)
        self.assertTrue(0 <= ev.ndcg_at(y, raw, ids, 2) <= 1)
        self.assertTrue(math.isnan(ev.ndcg_at(y[:1], raw[:1], ids[:1], 2)))
        self.assertAlmostEqual(ev.pairwise_accuracy(np.array([5.0, 1.0]), np.array([0.0, 0.0])), 0.5)

    def test_selection_rules_keep_smallest_weight_and_use_s339_incumbent(self) -> None:
        baseline = {"mse": 1.0, "ndcg2": 0.5, "stars2": 4.0, "low2": 0.1}
        better = {"mse": 0.9, "ndcg2": 0.5, "stars2": 3.95, "low2": 0.12}
        best = {"mse": 0.8, "ndcg2": 0.6, "stars2": 4.0, "low2": 0.1}
        safety = {"max_top2_stars_loss": 0.1, "max_low2_increase": 0.03}
        choice = ev.select_hybrid_weight({0.0: baseline, 0.1: better, 0.25: best}, safety)
        self.assertEqual(choice["content_weight"], 0.1)
        cold = ev.select_cold_head({"GBT120_s339": baseline, "STRUCTURED_DIRECT": baseline,
                                    "QWEN_DIRECT": better, "ALS_C2F": best, "FM150_s339": baseline}, safety)
        self.assertEqual(cold["head"], "ALS_C2F")

    @staticmethod
    def _metric_row(uid: int, model: str, group: str, mse: float, ndcg: float) -> dict:
        return {
            "model": model,
            "uid": uid,
            "cap": 10,
            "group": group,
            "h": 10,
            "h_group": "H_POSITIVE",
            "targets_total": 6,
            "targets_scored": 6,
            "complete": True,
            "mse": mse,
            "mae": math.sqrt(mse),
            "bias": 0.0,
            "pa": ndcg,
            "ndcg1": ndcg,
            "ndcg2": ndcg,
            "ndcg4": ndcg,
            "ndcg6": ndcg,
            "stars1": 4.0,
            "good1": 1.0,
            "low1": 0.0,
            "any_low1": 0.0,
            "both_low1": math.nan,
            "stars2": 4.0,
            "good2": 1.0,
            "low2": 0.0,
            "any_low2": 0.0,
            "both_low2": 0.0,
            "stars4": 4.0,
            "good4": 1.0,
            "low4": 0.0,
            "any_low4": 0.0,
            "both_low4": math.nan,
            "stars6": 4.0,
            "good6": 1.0,
            "low6": 0.0,
            "any_low6": 0.0,
            "both_low6": math.nan,
        }

    def test_three_seed_average_is_metric_average(self) -> None:
        rows = []
        for seed, mse in zip(ev.SEEDS, [1.0, 2.0, 3.0]):
            rows.append(self._metric_row(1, f"FM150_s{seed}", "ALL", mse, 0.5))
            rows.append(self._metric_row(1, f"GBT120_s{seed}", "ALL", mse + 1, 0.4))
        mean = ev.average_seed_metrics(pd.DataFrame(rows))
        fm = mean[mean.model.eq("FM150_SEED_MEAN")].iloc[0]
        self.assertEqual(fm.mse, 2.0)
        self.assertEqual(fm.targets_total, 6)

    def test_router_uses_cold_only_for_user_factor_with_cold_item(self) -> None:
        axis = pd.DataFrame({
            "h": [10] * 5,
            "primary_group": ["W_DIRECT", "C", "W_DIRECT", "C", "W_DIRECT"],
        })
        raw = {"ALS": np.array([1., np.nan, 1., np.nan, 1.]),
               "QWEN_DIRECT": np.array([2., 2., 2., 2., np.nan]),
               "STRUCTURED_DIRECT": np.array([3., 3., 3., 3., 3.]),
               "ALS_C2F": np.array([4., 4., np.nan, np.nan, 4.]),
               "GBT120_s339": np.array([5., 5., 5., 5., 5.])}
        available = {name: np.isfinite(value) for name, value in raw.items()}
        calibrated = {name: value.copy() for name, value in raw.items()}
        selection = {"warm": {"content_weight": 0.1},
                     "cold": {"head": "STRUCTURED_DIRECT"}}
        _, _, derived = ev.add_derived_models(axis, raw, available, calibrated, selection)
        self.assertEqual(derived["ROUTER_SOURCE"].tolist(), [
            "WARM:ALS_QWEN", "COLD:STRUCTURED_DIRECT",
            "FALLBACK:GBT120_s339", "FALLBACK:GBT120_s339",
            "FALLBACK:GBT120_s339",
        ])
        np.testing.assert_allclose(derived["ROUTER_s339"], [1.1, 3., 5., 5., 5.])

    def test_zero_weight_keeps_all_available_als_rows_without_qwen(self) -> None:
        axis = pd.DataFrame({"h": [10], "primary_group": ["W_DIRECT"]})
        raw = {"ALS": np.array([4.2]), "QWEN_DIRECT": np.array([np.nan]),
               "STRUCTURED_DIRECT": np.array([3.0]), "ALS_C2F": np.array([2.0]),
               "GBT120_s339": np.array([3.5])}
        available = {name: np.isfinite(value) for name, value in raw.items()}
        calibrated = {name: value.copy() for name, value in raw.items()}
        selection = {"warm": {"content_weight": 0.0},
                     "cold": {"head": "STRUCTURED_DIRECT"}}
        _, final_available, derived = ev.add_derived_models(
            axis, raw, available, calibrated, selection)
        self.assertTrue(final_available["ALS_QWEN"][0])
        self.assertEqual(derived["ALS_QWEN"][0], 4.2)
        self.assertEqual(derived["ROUTER_SOURCE"][0], "WARM:ALS")
        self.assertEqual(derived["ROUTER_s339"][0], 4.2)

    def test_formal_family_counts_independent_seeds_and_safe_label(self) -> None:
        rows = []
        for uid in range(1, 41):
            rows += [
                self._metric_row(uid, "ALS", "W_DIRECT", 1.0, 0.50),
                self._metric_row(uid, "ALS_QWEN", "W_DIRECT", 0.9, 0.51),
                self._metric_row(uid, "STRUCTURED_DIRECT", "C", 1.0, 0.50),
                self._metric_row(uid, "QWEN_DIRECT", "C", 0.9, 0.51),
                self._metric_row(uid, "ALS_C2F", "C", 0.8, 0.52),
                self._metric_row(uid, "FM150_SEED_MEAN", "C", 1.0, 0.50),
                self._metric_row(uid, "GBT120_SEED_MEAN", "C", 1.0, 0.50),
                self._metric_row(uid, "GBT120_s339", "C", 1.0, 0.50),
                self._metric_row(uid, "FM150_SEED_MEAN", "ALL", 1.0, 0.50),
                self._metric_row(uid, "GBT120_SEED_MEAN", "ALL", 1.0, 0.50),
                self._metric_row(uid, "GBT120_s339", "ALL", 1.0, 0.50),
                self._metric_row(uid, "ROUTER_s339", "ALL", 0.9, 0.51),
            ]
        frame = pd.DataFrame(rows)
        config = {
            "primary_cap": 10,
            "bootstrap": {"samples": 200, "seed": 346, "minimum_users": 30,
                          "shared_resamples_within_family": False,
                          "seed_derivation": "UINT64_BIG_ENDIAN_FIRST8_SHA256_UTF8(hybrid345-bootstrap-v1|346|family|after|before|metric)"},
            "safety": {"max_top2_stars_loss": 0.1, "max_low2_increase": 0.03},
            "formal_contrasts": [
                {"family": family, "group": group, "after": after, "before": before,
                 "metric": metric, "better": better, "confidence": confidence}
                for family, group, after, before, metric, better, confidence in ev.EXPECTED_FORMAL_CONTRASTS
            ],
            "decision_labels": ["ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM", "TRADEOFF", "NO_CLEAR_DIFFERENCE",
                                "DETECTED_HARM", "DESCRIPTIVE_SMALL_N"],
        }
        contrasts, paired, decisions = ev.formal_contrasts(frame, config)
        self.assertEqual(contrasts.family.value_counts().to_dict(), {"cold": 8, "router": 4, "warm": 2})
        self.assertTrue(contrasts.users.eq(40).all())
        self.assertEqual(contrasts.seed.nunique(), 14)
        self.assertFalse(paired.empty)
        self.assertEqual(set(row["verdict"] for row in decisions["pair_decisions"]),
                         {"ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM"})
        self.assertNotIn("CLEAR_WIN", json.dumps(decisions))

    def test_prediction_seal_and_availability_contract(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            names = np.asarray(ev.BASE_MODELS)
            values = np.ones((3, len(names)), dtype=np.float32)
            availability = np.ones_like(values, dtype=bool)
            np.savez(root / "predictions.npz", names=names, predictions=values, availability=availability)
            ev.write_json(root / "prediction-seal.json", {"scope": "DEVELOPMENT_ONLY", "labels_opened": False,
                          "names": list(ev.BASE_MODELS),
                          "files": {"predictions.npz": ev.pin(root / "predictions.npz")}})
            ev.verify_prediction_seal(root)
            raw, present = ev.load_prediction_matrix(root, 3)
            self.assertEqual(set(raw), set(ev.BASE_MODELS))
            self.assertTrue(all(vector.all() for vector in present.values()))
            with (root / "predictions.npz").open("ab") as handle:
                handle.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "sealed .* mismatch"):
                ev.verify_prediction_seal(root)

    def test_bad_prediction_gate_stops_before_any_parquet_read(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(
            pd, "read_parquet", side_effect=AssertionError("no parquet, especially labels, before score seal")
        ) as reader:
            with self.assertRaisesRegex(ValueError, "missing prediction seal"):
                ev.evaluate(Path(folder), Path(folder) / "out", ev.DEFAULT_CONFIG)
            reader.assert_not_called()

    def test_evaluation_review_fingerprint_includes_reporter(self) -> None:
        fingerprint = ev.evaluation_fingerprint()
        self.assertIn("scripts/report_hybrid345.py", fingerprint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
