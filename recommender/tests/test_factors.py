from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

from feelm_recommender import (
    ArtifactCompatibilityError, ArtifactKind, ArtifactValidationError, ItemFactorModel
)
from helpers import metadata


class ItemFactorModelTest(unittest.TestCase):
    def test_fold_in_matches_explicit_als_wr_and_scores_unknown_as_missing(self) -> None:
        model = ItemFactorModel(
            item_ids=np.array([1, 2]), factors=np.array([[1.0, 0.0], [0.0, 1.0]]),
            reg_param=0.5
        )
        folded = model.fold_in([1, 2], [4.0, 2.0])
        self.assertIsNotNone(folded.factor)
        np.testing.assert_allclose(folded.factor, [2.0, 1.0])
        scores, known = model.score(folded.factor, [1, 9])
        self.assertAlmostEqual(scores[0], 2.0)
        self.assertTrue(np.isnan(scores[1]))
        self.assertEqual(known.tolist(), [True, False])

    def test_fold_in_ignores_unknown_factor_inputs_but_reports_coverage(self) -> None:
        model = ItemFactorModel(np.array([1]), np.array([[1.0, 0.0]]), 0.1)
        result = model.fold_in([1, 99], [4.0, 2.0])
        self.assertEqual(result.provided_count, 2)
        self.assertEqual(result.factor_count, 1)
        self.assertTrue(result.available)

    def test_factor_model_sorts_ids_and_rejects_duplicates(self) -> None:
        model = ItemFactorModel(np.array([2, 1]), np.array([[2.0], [1.0]]), 0.1)
        values, known = model.lookup([1, 2])
        np.testing.assert_allclose(values[:, 0], [1.0, 2.0])
        self.assertTrue(known.all())
        with self.assertRaisesRegex(ArtifactValidationError, "unique"):
            ItemFactorModel(np.array([1, 1]), np.array([[1.0], [2.0]]), 0.1)

    def test_factor_loader_accepts_movie_names_and_validates_rank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "factors.npz"
            np.savez_compressed(
                payload, user_ids=np.array([1]), user_factors=np.array([[1.0, 1.0]]),
                movie_ids=np.array([2]), movie_factors=np.array([[2.0, 3.0]])
            )
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            model = ItemFactorModel.load_npz(
                payload,
                metadata(ArtifactKind.ALS_ITEM_FACTORS, checksum=digest, factor_rank=2),
            )
            self.assertEqual(model.rank, 2)
            with self.assertRaisesRegex(ArtifactCompatibilityError, "rank mismatch"):
                ItemFactorModel.load_npz(
                    payload,
                    metadata(
                        ArtifactKind.ALS_ITEM_FACTORS, checksum=digest, factor_rank=3
                    ),
                )


if __name__ == "__main__":
    unittest.main()

