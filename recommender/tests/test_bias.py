from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

from feelm_recommender import ArtifactKind, ArtifactValidationError, BiasModel
from helpers import metadata


class BiasModelTest(unittest.TestCase):
    def test_regularized_bias_uses_known_effects_and_unknown_fallback(self) -> None:
        model = BiasModel.fit(
            np.array([1, 1, 2, 2]), np.array([1, 2, 1, 2]),
            np.array([5.0, 4.0, 2.0, 1.0]), reg_user=0.1, reg_item=0.1, iterations=5
        )
        predictions = model.predict(np.array([1, 2, 99]), np.array([1, 2, 99]))
        self.assertGreater(predictions[0], predictions[1])
        self.assertAlmostEqual(predictions[2], 3.0)

    def test_onboarding_bias_and_popularity_have_independent_scores(self) -> None:
        model = BiasModel.fit(
            [0, 0, 1, 1, 1], [0, 1, 0, 0, 1], [5, 1, 5, 4, 1], iterations=3
        )
        stars = model.predict_for_onboarding_user([0, 1], [0], [5.0])
        popularity = model.popularity([0, 1], prior_count=0)
        self.assertEqual(stars.shape, (2,))
        self.assertEqual(popularity.shape, (2,))
        self.assertGreater(popularity[0], popularity[1])

    def test_bias_rejects_invalid_ratings_and_ids(self) -> None:
        with self.assertRaises(ValueError):
            BiasModel.fit([-1], [0], [3.0])
        with self.assertRaises(ValueError):
            BiasModel.fit([0], [0], [6.0])

    def test_load_legacy_experiment_npz_requires_matching_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "bias.npz"
            np.savez_compressed(
                payload, global_mean=np.array(3.0), user_counts=np.array([1]),
                user_sums=np.array([3.0]), movie_counts=np.array([1]),
                movie_sums=np.array([3.0]), user_bias=np.array([0.0]),
                movie_bias=np.array([0.0])
            )
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            model = BiasModel.load_npz(payload, metadata(ArtifactKind.BIAS, checksum=digest))
            self.assertEqual(model.global_mean, 3.0)
            with self.assertRaisesRegex(ArtifactValidationError, "checksum mismatch"):
                BiasModel.load_npz(payload, metadata(ArtifactKind.BIAS))


if __name__ == "__main__":
    unittest.main()

