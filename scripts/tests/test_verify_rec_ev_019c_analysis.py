from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from verify_rec_ev_019c_analysis import _find_summary_row, _paired_bootstrap_ci


class RecEv019cAnalysisVerifierTest(unittest.TestCase):
    def test_verifier_bootstrap_is_deterministic(self) -> None:
        values = np.asarray([-0.1, 0.2, 0.3], dtype=np.float64)
        first = _paired_bootstrap_ci(values, seed=11, iterations=500)
        second = _paired_bootstrap_ci(values, seed=11, iterations=500)
        self.assertEqual(first, second)

    def test_core_slice_lookup_rejects_duplicate_rows(self) -> None:
        row = {
            "k": 5,
            "dimension": "language_group",
            "cohort": "한국어 원어",
            "model_id": "B8_LIGHTFM",
        }
        with self.assertRaisesRegex(RuntimeError, "missing or duplicate"):
            _find_summary_row(
                [row, dict(row)],
                k=5,
                dimension="language_group",
                cohort="한국어 원어",
                model_id="B8_LIGHTFM",
            )


if __name__ == "__main__":
    unittest.main()
