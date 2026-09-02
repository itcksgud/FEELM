from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from scipy import sparse


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_lightfm import build_lightfm_item_features


class RecEv019CLightfmTest(unittest.TestCase):
    def test_item_features_include_identity_and_metadata(self) -> None:
        metadata = sparse.csr_matrix(np.asarray([[1, 0], [0, 1], [0, 0]], dtype=np.float32))
        features = build_lightfm_item_features(metadata)
        self.assertEqual((3, 5), features.shape)
        np.testing.assert_array_equal(features[:, :3].toarray(), np.eye(3, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
