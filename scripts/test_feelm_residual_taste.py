"""Analytic and leakage-boundary tests for the fixed REC044 diagnostic."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy import sparse

import feelm_residual_taste as core
import rec_ev_044_residual_taste as runner
from feelm_preference_structure import write_json


def fixture():
    ids = np.arange(1, 81)
    member = np.zeros((80, 34), dtype=np.uint8)
    for i in range(80):
        member[i, i % 2] = member[i, 8 + i % 2] = member[i, 16 + i % 2] = 1
    return dict(
        user_keys=np.array(["a", "b"]),
        movie_ids=ids,
        membership=member,
        bayes=np.full(80, 3.0),
        training_counts=np.full(80, 10),
        o_index=np.array([np.arange(30), np.arange(30, 60)]),
        o_ratings=np.array([[4.0, 2.0] * 15, [2.0, 4.0] * 15]),
        e_index=np.r_[np.arange(60, 70), np.arange(70, 80)],
        e_offsets=np.array([0, 10, 20]),
        group_codes=np.array([str(i) for i in range(34)]),
    )


class ProbeTests(unittest.TestCase):
    def test_analytic_shrink(self):
        x = sparse.csr_matrix(np.eye(2)[[0, 0, 1, 1]])
        a = core.ridge_alpha(x, np.array([1, 1, -1, -1]), 5)
        np.testing.assert_allclose((x.T @ a), [2 / 7, -2 / 7])

    def test_dual_equals_primal(self):
        x = np.array([[1.0, 0], [0.5, 0.5], [0, 1]])
        r = np.array([1.0, -0.5, -0.5])
        a = core.ridge_alpha(sparse.csr_matrix(x), r, 5)
        np.testing.assert_allclose(
            x.T @ a, np.linalg.solve(x.T @ x + 5 * np.eye(2), x.T @ r)
        )

    def test_zero_row_is_zero_effect(self):
        x = sparse.csr_matrix([[1.0, 0], [0, 0]])
        a = core.ridge_alpha(x, np.array([1.0, -1]), 5)
        self.assertEqual(float((x @ x.T @ a)[1]), 0)

    def test_feature_normalization_and_missing_combination(self):
        d = fixture()
        keys = [[7, 7, 8]] * 79 + [[]]
        xs, vocab = core.features(d, keys)
        self.assertEqual(vocab["keyword_df"], [79, 79])
        self.assertEqual(vocab["filtered_keyword_missing"], 1)
        self.assertEqual(xs[3][-1].nnz, 0)
        self.assertAlmostEqual(float(xs[4][-1].multiply(xs[4][-1]).sum()), 0.5)
        self.assertAlmostEqual(float(xs[3][0].multiply(xs[3][0]).sum()), 1)

    def test_label_free_fit_and_opposite_users(self):
        d = fixture()
        xs, _ = core.features(d, [[i % 2] for i in range(80)])
        f = core.fit(d, xs)
        np.testing.assert_allclose(f["own"][:10, 0] - 3, [0.75, -0.75] * 5)
        np.testing.assert_allclose(f["donor"][:10, 0] - 3, [-0.75, 0.75] * 5)
        self.assertNotIn("rating_raw", d)

    def test_user_habit_removed(self):
        d = fixture()
        d["o_ratings"][:] = 4
        xs, _ = core.features(d, [[i % 2] for i in range(80)])
        f = core.fit(d, xs)
        np.testing.assert_allclose(f["own"], 4)
        np.testing.assert_allclose(f["alpha"], 0)

    def test_unseen_keyword_does_not_mean_dislike(self):
        d = fixture()
        xs, _ = core.features(d, [[i % 2] if i < 60 else [] for i in range(80)])
        f = core.fit(d, xs)
        np.testing.assert_array_equal(f["own"][:, 3], f["baseline"])

    def test_no_O_E_overlap(self):
        d = fixture()
        d["e_index"][0] = 0
        xs, _ = core.features(d, [[i % 2] for i in range(80)])
        with self.assertRaisesRegex(ValueError, "overlap"):
            core.fit(d, xs)

    def test_invalid_rating(self):
        d = fixture()
        d["o_ratings"][0, 0] = 3.2
        xs, _ = core.features(d, [[i % 2] for i in range(80)])
        with self.assertRaisesRegex(ValueError, "O30"):
            core.fit(d, xs)

    def test_family_and_bootstrap_null(self):
        self.assertEqual(len(core.CONTRASTS), 9)
        draws, intervals = core.bootstrap(np.zeros((10, 9)), repeats=20)
        self.assertTrue((draws == 0).all() and (intervals == 0).all())

    def test_evaluation_known_result_and_axes(self):
        d = fixture()
        xs, _ = core.features(d, [[i % 2] for i in range(80)])
        f = core.fit(d, xs)
        labels = {k: d[k] for k in ("user_keys", "e_index", "e_offsets")}
        labels["rating_raw"] = np.array([4.0, 2.0] * 5 + [2.0, 4.0] * 5)
        with patch.object(
            core, "bootstrap", return_value=(np.zeros((1, 9)), np.zeros((9, 2)))
        ):
            s, _ = core.evaluate(d, f, labels)
        self.assertAlmostEqual(s["mse"][0], 1)
        self.assertAlmostEqual(s["mse"][1], 0.0625)
        self.assertAlmostEqual(s["mae"][1], 0.25)
        self.assertEqual(s["donor_O_recipient_E_overlap"], 0)
        labels["user_keys"] = labels["user_keys"][::-1]
        with self.assertRaisesRegex(ValueError, "label axis"):
            core.evaluate(d, f, labels)

    def test_E_not_decoded_without_seal(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner.pd, "read_parquet") as reader,
        ):
            with self.assertRaises(FileNotFoundError):
                runner.labels_after_seal(Path(tmp), {}, {})
            reader.assert_not_called()

    def test_stale_prediction_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_json(
                out / "prediction-seal.json",
                dict(fingerprint={}, E_values_opened=False),
            )
            with patch.object(runner, "fingerprint", return_value={"x": "new"}):
                with self.assertRaisesRegex(ValueError, "code seal"):
                    runner.verify_prediction(out)


if __name__ == "__main__":
    unittest.main()
