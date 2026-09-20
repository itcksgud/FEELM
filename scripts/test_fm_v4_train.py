from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fm_v4_evaluate import _read_labels, _read_prediction, evaluate_frame, hierarchical_point
from fm_v4_train import (
    AdditiveModel,
    field_contribution,
    fit_interaction_only,
    initialize_interaction,
    interaction_objective_and_gradients,
    positive_n_user_macro_mse,
    write_predictions,
)


def schema() -> dict:
    return {
        "ordered_names": [f"f{index}" for index in range(11)],
        "profiles": {"matched_additive_v4": {"linear_indices": list(range(11))}},
        "interaction_fields": {
            "genre": {"candidate_indices": [0, 1], "q_indices": [2, 3], "gate_index": 4},
            "tag": {"candidate_indices": [5, 6], "q_indices": [7, 8], "gate_index": 9},
        },
    }


def matrix() -> sparse.csr_matrix:
    return sparse.csr_matrix(np.asarray([
        [0.7, 0.3, 0.4, -0.2, 0.8, 0.0, 1.0, -0.3, 0.2, 0.5, 1.0],
        [0.2, 0.8, -0.1, 0.5, 0.9, 0.6, 0.4, 0.7, -0.4, 0.7, -0.2],
        [1.0, 0.0, 0.3, -0.6, 0.4, 0.2, 0.8, -0.1, 0.5, 0.6, 0.3],
    ], dtype=np.float64))


class FMV4TrainTests(unittest.TestCase):
    def test_low_rank_diagonal_is_masked(self) -> None:
        additive = AdditiveModel(0.0, np.zeros(11))
        model = initialize_interaction(additive, schema(), seed=623)
        field = model.fields["genre"]
        field.diagonal[:] = 0.0
        field.left[:] = np.asarray([[1.0, 2.0, 0.0, 0.0], [3.0, 4.0, 0.0, 0.0]])
        field.right[:] = np.asarray([[5.0, 6.0, 0.0, 0.0], [7.0, 8.0, 0.0, 0.0]])
        values = np.zeros((2, 11), dtype=float)
        values[0, [0, 2, 4]] = 1.0  # candidate-0 x Q-0 is a forbidden low-rank diagonal.
        values[1, [0, 3, 4]] = 1.0  # candidate-0 x Q-1 is an allowed off-diagonal.
        contribution = field_contribution(sparse.csr_matrix(values), field)
        self.assertEqual(contribution[0], 0.0)
        self.assertEqual(contribution[1], float(np.dot(field.left[0], field.right[1])))

    def test_signed_interaction_gradient_matches_finite_difference(self) -> None:
        X = matrix()
        additive = AdditiveModel(2.9, np.linspace(-0.1, 0.1, X.shape[1]))
        model = initialize_interaction(additive, schema(), seed=623)
        rng = np.random.default_rng(77)
        for field in model.fields.values():
            field.diagonal[:] = rng.normal(0, 0.03, len(field.diagonal))
            field.left[:] = rng.normal(0, 0.03, field.left.shape)
            field.right[:] = rng.normal(0, 0.03, field.right.shape)
        y = np.asarray([3.5, 2.0, 4.5])
        weight = np.asarray([0.2, 0.3, 0.5])
        reg = 0.01
        _, gradients = interaction_objective_and_gradients(model, X, y, weight, reg)
        epsilon = 1e-6
        for field_name in ("genre", "tag"):
            for parameter in ("diagonal", "left", "right"):
                values = getattr(model.fields[field_name], parameter)
                for index in np.ndindex(values.shape):
                    original = values[index]
                    values[index] = original + epsilon
                    plus = interaction_objective_and_gradients(model, X, y, weight, reg)[0]
                    values[index] = original - epsilon
                    minus = interaction_objective_and_gradients(model, X, y, weight, reg)[0]
                    values[index] = original
                    numeric = (plus - minus) / (2 * epsilon)
                    self.assertAlmostEqual(numeric, gradients[field_name][parameter][index], places=6)

    def test_optimizer_freezes_additive_and_n0_gate_is_exact_zero(self) -> None:
        X = matrix()
        X = sparse.vstack([X, X[0]], format="csr")
        # Last row represents N=0: Q may be populated, but both field gates are exactly zero.
        X[3, 4] = 0.0
        X[3, 9] = 0.0
        additive = AdditiveModel(3.0, np.linspace(-0.02, 0.02, X.shape[1]))
        initial = initialize_interaction(additive, schema(), seed=623)
        np.testing.assert_array_equal(initial.predict(X), additive.predict(X))
        metadata = pd.DataFrame({"uid": [1, 1, 2], "target_key": ["a", "b", "c"], "n": [1, 2, 1]})
        optimizer = {"learning_rate": 0.01, "max_epochs": 3, "patience": 3, "beta1": 0.9,
                     "beta2": 0.999, "epsilon": 1e-8, "gradient_clip_norm": 10.0}
        fitted, _ = fit_interaction_only(
            X[:3], np.asarray([4.0, 2.0, 4.5]), np.asarray([1 / 3, 1 / 3, 1 / 3]),
            X[:3], np.asarray([4.0, 2.0, 4.5]), metadata,
            initial, 0.001, optimizer, max_epochs=2, restore_best=False,
        )
        self.assertEqual(fitted.intercept, additive.intercept)
        np.testing.assert_array_equal(fitted.linear, additive.linear)
        self.assertEqual(float(fitted.predict(X[3])[0]), float(additive.predict(X[3])[0]))

    def test_hierarchical_user_target_weighting(self) -> None:
        metadata = pd.DataFrame({
            "uid": [1, 1, 1, 2, 2], "target_key": ["a", "a", "b", "c", "c"],
            "n": [1, 2, 1, 1, 2],
        })
        actual = np.zeros(5)
        prediction = np.sqrt(np.asarray([1.0, 3.0, 4.0, 8.0, 10.0]))
        # uid1: mean(target a=2,target b=4)=3; uid2: target c=9; users mean to 6.
        self.assertAlmostEqual(positive_n_user_macro_mse(metadata, actual, prediction), 6.0)
        frame = metadata.copy()
        frame["delta"] = np.asarray([1.0, 3.0, 4.0, 8.0, 10.0])
        self.assertAlmostEqual(hierarchical_point(frame, "delta"), 6.0)

    def test_validation_label_firewall_rejects_embedded_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            table = pa.table({
                "target_key": ["a"], "uid": [1], "n": [1], "n_bucket": ["1"],
                "label": [4.0], "feature_indices": [[0]], "feature_values": [[1.0]],
                "feature_size": [1],
            })
            input_path = root / "validation.parquet"
            pq.write_table(table, input_path)
            with self.assertRaisesRegex(RuntimeError, "label firewall"):
                write_predictions(input_path, root / "out", lambda X: np.zeros(X.shape[0]), 1, [0.5, 5.0])

            pq.write_table(table.drop(["label"]).append_column("target_rating", pa.array([4.0])),
                           root / "validation-target-rating.parquet")
            with self.assertRaisesRegex(RuntimeError, "label firewall"):
                write_predictions(root / "validation-target-rating.parquet", root / "out-2",
                                  lambda X: np.zeros(X.shape[0]), 1, [0.5, 5.0])

            prediction_root = root / "predictions"
            prediction_root.mkdir()
            pq.write_table(pa.table({
                "target_key": ["a"], "uid": [1], "n": [1], "label": [4.0],
                "raw_prediction": [3.0], "prediction": [3.0],
            }), prediction_root / "part.parquet")
            with self.assertRaisesRegex(RuntimeError, "label firewall"):
                _read_prediction(prediction_root, "additive")

            prediction_rating_root = root / "predictions-target-rating"
            prediction_rating_root.mkdir()
            pq.write_table(pa.table({
                "target_key": ["a"], "uid": [1], "n": [1], "target_rating": [4.0],
                "raw_prediction": [3.0], "prediction": [3.0],
            }), prediction_rating_root / "part.parquet")
            with self.assertRaisesRegex(RuntimeError, "label firewall"):
                _read_prediction(prediction_rating_root, "additive")

            labels_path = root / "validation-labels.jsonl"
            labels_path.write_text(json.dumps({"target_key": "a", "target_rating": 4.0}) + "\n",
                                   encoding="utf-8")
            labels = _read_labels(labels_path)
            self.assertEqual(labels.to_dict("records"), [{"target_key": "a", "label": 4.0}])
            labels_path.write_text(json.dumps({"target_key": "a", "label": 4.0}) + "\n",
                                   encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "target_rating"):
                _read_labels(labels_path)

    def test_point_positive_but_below_point_three_percent_is_immaterial(self) -> None:
        rows = []
        factor_error = math.sqrt(0.998)
        for uid in range(1, 5):
            rows.extend([
                {"target_key": f"{uid}-a", "uid": uid, "n": 0, "label": 4.0,
                 "raw_prediction_additive": 3.0, "prediction_additive": 3.0,
                 "raw_prediction_factor": 3.0, "prediction_factor": 3.0},
                {"target_key": f"{uid}-a", "uid": uid, "n": 1, "label": 4.0,
                 "raw_prediction_additive": 3.0, "prediction_additive": 3.0,
                 "raw_prediction_factor": 4.0 - factor_error, "prediction_factor": 4.0 - factor_error},
            ])
        config = {
            "prediction_bounds": [0.5, 5.0],
            "primary": {
                "bootstrap_replicates": 100, "bootstrap_seed": 4623001, "tie_tolerance": 1e-12,
                "minimum_relative_mse_improvement": 0.003, "n_cell_minimum_users": 200,
                "n_cell_mse_delta_upper_limit": 0.005, "mae_delta_upper_limit": 0.005,
                "maximum_clipping_fraction_increase": 0.001,
            },
        }
        result = evaluate_frame(pd.DataFrame(rows), config)
        self.assertEqual(result["primary"]["outcome"], "STATISTICALLY_POSITIVE_BUT_IMMATERIAL")
        self.assertEqual(result["retention_decision"], "KEEP_MATCHED_ADDITIVE_BASELINE")
        self.assertTrue(result["safety"]["n0"]["pass"])


if __name__ == "__main__":
    unittest.main()
