from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hybrid345_encode import (  # noqa: E402
    _atomic_save_npy,
    last_token_pool,
    make_consecutive_batches,
    pilot_indices,
    validate_embeddings,
)


class Hybrid345EncodeTests(unittest.TestCase):
    def test_last_token_pool_supports_left_and_right_padding(self) -> None:
        import torch

        hidden = torch.tensor(
            [
                [[10.0], [11.0], [12.0], [13.0]],
                [[20.0], [21.0], [22.0], [23.0]],
            ]
        )
        left_mask = torch.tensor([[0, 0, 1, 1], [0, 1, 1, 1]])
        np.testing.assert_array_equal(last_token_pool(hidden, left_mask).numpy().ravel(), [13.0, 23.0])
        right_mask = torch.tensor([[1, 1, 0, 0], [1, 1, 1, 0]])
        np.testing.assert_array_equal(last_token_pool(hidden, right_mask).numpy().ravel(), [11.0, 22.0])

    def test_batches_keep_canonical_order_without_length_sorting(self) -> None:
        selected = np.arange(19, dtype=np.int64)
        batches = make_consecutive_batches(selected, batch_size=8)
        self.assertEqual([batch.tolist() for batch in batches], [
            list(range(8)), list(range(8, 16)), list(range(16, 19))
        ])
        with self.assertRaisesRegex(RuntimeError, "canonical"):
            make_consecutive_batches(selected[::-1], batch_size=8)

    def test_pilot_selection_is_stable_and_returned_in_catalog_order(self) -> None:
        ids = np.arange(101, 133, dtype=np.int64)
        one = pilot_indices(ids, size=16)
        two = pilot_indices(ids, size=16)
        np.testing.assert_array_equal(one, two)
        self.assertTrue(np.all(one[1:] > one[:-1]))
        for start in range(0, len(one), 8):
            np.testing.assert_array_equal(one[start:start + 8],
                                          np.arange(one[start], one[start] + 8))

    def test_validation_and_atomic_npy(self) -> None:
        values = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        result = validate_embeddings(values, rows=2, dimension=2)
        self.assertEqual(result["shape"], [2, 2])
        with self.assertRaisesRegex(RuntimeError, "normalization"):
            validate_embeddings(values * 2, rows=2, dimension=2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "values.npy"
            _atomic_save_npy(path, values)
            np.testing.assert_array_equal(np.load(path, allow_pickle=False), values)


if __name__ == "__main__":
    unittest.main()
