from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_rating_scale_alignment import (  # noqa: E402
    affine,
    analyze,
    clamp,
    round_half_up,
)


class RatingScaleAlignmentTest(unittest.TestCase):
    def test_candidate_transforms_have_declared_range_and_loss_properties(self) -> None:
        values = np.array([0.5, 0.75, 1.0, 3.0, 5.0])
        np.testing.assert_allclose(clamp(values), [1, 1, 1, 3, 5])
        np.testing.assert_allclose(round_half_up(values), [1, 1, 1, 3, 5])
        np.testing.assert_allclose(affine(values)[[0, -1]], [1, 5])
        self.assertTrue(bool((np.diff(affine(values)) > 0).all()))

    def test_analysis_uses_held_out_rows_and_fails_closed_without_c1_labels(self) -> None:
        frame = pd.DataFrame(
            {
                "user_id": [1, 1, 2],
                "rating": [0.5, 3.5, 5.0],
                "timestamp": [9, 10, 11],
                **{
                    f"prediction_k{k}": [0.5, 3.25, 4.75]
                    for k in (1, 3, 5, 10, 20)
                },
            }
        )
        manifest = analyze(
            {"protocol": {"star_selection_boundary": 10}}, frame
        )
        self.assertEqual(manifest["source_data"]["evaluation_rows"], 2)
        self.assertFalse(manifest["source_data"]["has_paired_c1_integer_labels"])
        self.assertEqual(
            manifest["decision"]["selected"], "STAR_DISABLED_FAIL_CLOSED"
        )
        self.assertEqual(
            manifest["options"]["CLAMP_1_TO_5"]["technical_properties"][
                "decision"
            ],
            "REJECT",
        )
        self.assertEqual(
            manifest["options"]["AFFINE_0_5_TO_5_INTO_1_TO_5"][
                "technical_properties"
            ]["decision"],
            "DO_NOT_ADOPT_WITHOUT_C1_PAIRED_VALIDATION",
        )


if __name__ == "__main__":
    unittest.main()
