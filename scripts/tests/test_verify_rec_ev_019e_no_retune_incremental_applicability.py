from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scripts.verify_rec_ev_019e_no_retune_incremental_applicability import (
    benefit_harm_counts,
    bootstrap_paired,
    decision,
    route_for_stratum,
)


class RecEv019eVerifierTests(unittest.TestCase):
    def test_independent_routing_mapping(self) -> None:
        self.assertEqual(route_for_stratum("BOTH_LIGHTFM"), ("K5", "K5_FOLD_IN"))
        self.assertEqual(route_for_stratum("K10_NEWLY_APPLICABLE"), ("K10", "K10_FOLD_IN"))
        self.assertEqual(route_for_stratum("BOTH_FALLBACK"), ("K5", "B0"))

    def test_benefit_harm_counts_are_exact(self) -> None:
        frame = pd.DataFrame({"delta_ndcg_at_10": [0.1, 0.0, -0.2, 0.3]})
        self.assertEqual(benefit_harm_counts(frame), {"benefit": 2, "neutral": 1, "harm": 1})

    def test_bootstrap_and_limited_decision_are_deterministic(self) -> None:
        values = bootstrap_paired(
            np.asarray([0.01, 0.02, 0.03]),
            np.asarray([0.0, 0.0, 0.0]),
            iterations=100,
            seed=20260924,
        )
        self.assertEqual(values, bootstrap_paired(
            np.asarray([0.01, 0.02, 0.03]),
            np.asarray([0.0, 0.0, 0.0]),
            iterations=100,
            seed=20260924,
        ))
        self.assertEqual(decision(values)["status"], "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()
