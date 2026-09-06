from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts.run_rec_ev_027_replication import (
    DEFAULT,
    OUTERS,
    ROOT,
    ResumeError,
    artifact,
    benefit,
    classify_replication_truth,
    global_paths,
    missing_max_t,
    outer_paths,
    outer_masks_for,
    top2_seed_stability,
    verify_global_prelabel_score_seal,
)
from scripts.run_rec_ev_027_screen import common_features


class RecEv027ReplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(DEFAULT.read_text(encoding="utf-8"))
        cls.item_ids, _, _ = common_features(cls.contract)

    def test_proxy_masks_match_preflight_and_are_disjoint(self) -> None:
        kr_warm, kr_cold = outer_masks_for(self.contract, self.item_ids, "KOREAN_ORIGIN_COLD", "KR")
        recent_warm, recent_cold = outer_masks_for(
            self.contract, self.item_ids, "RELEASE_2020_2023_COLD", "2020_2023"
        )
        self.assertEqual(1177, int(kr_cold.sum()))
        self.assertEqual(7988, int(recent_cold.sum()))
        self.assertFalse(bool((kr_warm & kr_cold).any()))
        self.assertFalse(bool((recent_warm & recent_cold).any()))
        self.assertGreater(int((~recent_warm & ~recent_cold).sum()), 0)

    def test_random_mask_is_complete_partition(self) -> None:
        warm, cold = outer_masks_for(self.contract, self.item_ids, "RANDOM_ITEM_COLD", "1")
        self.assertTrue(np.all(warm ^ cold))
        self.assertEqual(len(self.item_ids), int(warm.sum() + cold.sum()))

    def test_seed_top2_overlap(self) -> None:
        self.assertEqual(0.5, top2_seed_stability([[1, 2], [2, 3], [1, 3]]))

    def test_benefit_orients_harm_down_and_q_up(self) -> None:
        model = np.asarray([0.1, 0.2])
        comparator = np.asarray([0.3, 0.4])
        np.testing.assert_allclose(benefit(model, comparator, "HARM20"), [0.2, 0.2])
        np.testing.assert_allclose(benefit(comparator, model, "TOP2_MEAN_Q"), [0.2, 0.2])

    def test_missing_max_t_is_deterministic_and_keeps_zero_se(self) -> None:
        values = np.column_stack([
            np.arange(100, dtype=float),
            np.where(np.arange(100) < 50, np.arange(100, dtype=float), np.nan),
            np.full(100, 2.0),
        ])
        one = missing_max_t(values, repeats=50, seed=9)
        two = missing_max_t(values, repeats=50, seed=9)
        np.testing.assert_allclose(one["low"], two["low"])
        self.assertEqual(one["point"][2], one["low"][2])
        self.assertEqual(one["point"][2], one["high"][2])

    def test_direct_increment_active_failure_alone_is_inconclusive(self) -> None:
        self.assertEqual(
            "INCONCLUSIVE_DIRECT_INCREMENT",
            classify_replication_truth(
                active_ok=False, lower_ok=True, direction_ok=True,
                upper_fail=False, random_comparison=False,
            ),
        )
        self.assertEqual(
            "FAIL_RANDOM_REPLICATION",
            classify_replication_truth(
                active_ok=False, lower_ok=True, direction_ok=True,
                upper_fail=False, random_comparison=True,
            ),
        )

    def test_global_score_seal_recursively_verifies_score_artifacts(self) -> None:
        outputs = ROOT / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=outputs) as temporary:
            output_root = Path(temporary)
            g = global_paths(output_root)
            outer_specs = []
            first_score = None
            for slug, _, _ in OUTERS:
                p = outer_paths(output_root, slug)
                p["scores"].parent.mkdir(parents=True, exist_ok=True)
                p["scores"].write_bytes(f"score-{slug}".encode())
                if first_score is None:
                    first_score = p["scores"]
                p["score_integrity"].write_text(json.dumps({
                    "outer": slug,
                    "score_ranks": artifact(p["scores"]),
                }), encoding="utf-8")
                outer_specs.append(artifact(p["score_integrity"]))
            g["scores"].parent.mkdir(parents=True, exist_ok=True)
            g["scores"].write_text(json.dumps({
                "target_rating_values_opened": False,
                "outer_integrities": outer_specs,
            }), encoding="utf-8")
            verify_global_prelabel_score_seal(output_root, g)
            assert first_score is not None
            first_score.write_bytes(b"tampered")
            with self.assertRaises(ResumeError):
                verify_global_prelabel_score_seal(output_root, g)


if __name__ == "__main__":
    unittest.main()
