from __future__ import annotations

import json
import unittest

import pandas as pd

from scripts.run_rec_ev_022b_stage2 import ANCHORS, ENCODINGS, EVEN_K, build_contrasts, build_parser
from scripts.validate_rec_ev_022b_contract import CONTRACT, validate_contract


class RecEv022bStage2Tests(unittest.TestCase):
    def test_committed_contract_has_audited_truth_table(self) -> None:
        result = validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8")))
        self.assertEqual("PASS_REC_EV_022B_CONTRACT", result["status"])
        self.assertEqual(456, result["expected_contrasts"])
        self.assertFalse(result["final_reserve_access"])

    def test_exact_primary_family_has_456_contrasts(self) -> None:
        rows = []
        for user_index, user in enumerate(("a" * 64, "b" * 64)):
            for encoding_index, encoding in enumerate(ENCODINGS):
                for anchor_index, anchor in enumerate(ANCHORS):
                    for k in (0,) + EVEN_K:
                        rows.append({
                            "user_key": user,
                            "encoding": encoding,
                            "anchor": anchor,
                            "k": k,
                            "pair1_mean_q": 0.5 + 0.001 * k + 0.01 * user_index + 0.001 * encoding_index,
                            "pair1_worst_q_loss": 0.5 - 0.001 * k + 0.01 * anchor_index,
                        })
        values, metadata, users = build_contrasts(pd.DataFrame(rows))
        self.assertEqual((2, 456), values.shape)
        self.assertEqual(456, len(metadata))
        self.assertEqual(["a" * 64, "b" * 64], users)

    def test_cli_cannot_open_test_or_final_phase(self) -> None:
        choices = tuple(next(action for action in build_parser()._actions if action.dest == "phase").choices)
        self.assertNotIn("test", choices)
        self.assertNotIn("final", choices)


if __name__ == "__main__":
    unittest.main()
