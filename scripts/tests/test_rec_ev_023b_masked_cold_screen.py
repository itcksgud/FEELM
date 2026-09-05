from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.rec_ev_022a_core import old_user_bucket, user_key, user_role_bucket
from scripts.run_rec_ev_023b_masked_cold_screen import (
    HEADS,
    _first_pass,
    _head_tie_digest,
    analytic_random_top2,
    build_contrasts,
    build_parser,
    item_bucket,
    strict_head_order,
)
from scripts.validate_rec_ev_023b_contract import CONTRACT, validate_contract


class RecEv023bMaskedColdScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_is_adaptive_pseudo_cold_only(self) -> None:
        result = validate_contract(self.contract)
        self.assertEqual("PASS_REC_EV_023B_CONTRACT", result["status"])
        self.assertTrue(result["adaptive_stage1"])
        self.assertFalse(result["locked_test_access"])
        self.assertIsNone(result["champion"])
        self.assertIn("STRICT_COLD_START", self.contract["claim_boundary"]["forbidden"])

    def test_item_bucket_uses_full_digest_big_endian(self) -> None:
        salt = self.contract["item_split"]["salt"]
        values = [item_bucket(salt, movie) for movie in (1, 10, 999, 200000)]
        self.assertEqual(values, [item_bucket(salt, movie) for movie in (1, 10, 999, 200000)])
        self.assertTrue(all(0 <= value < 10000 for value in values))

    def test_analytic_random_top2_matches_pair_enumeration(self) -> None:
        q = np.asarray([0.1, 0.5, 0.9], dtype=np.float64)
        utility, loss = analytic_random_top2(q)
        self.assertAlmostEqual(float(q.mean()), utility)
        expected = np.mean([1.0 - min(q[i], q[j]) for i in range(3) for j in range(i + 1, 3)])
        self.assertAlmostEqual(float(expected), loss)

    def test_tie_order_is_label_blind_and_exact(self) -> None:
        movies = np.asarray([30, 10, 20], dtype=np.int64)
        scores = np.asarray([1.0, 1.0, 1.0], dtype=np.float64)
        key = "a" * 64
        order = strict_head_order(
            self.contract, movies, scores, user=key, head="E5", encoding="BINARY_SIGN", k=6,
        )
        expected = sorted(
            range(3),
            key=lambda index: (
                _head_tie_digest(self.contract, "E5", "BINARY_SIGN", 6, key, int(movies[index])),
                int(movies[index]),
            ),
        )
        self.assertEqual(expected, order.tolist())
        self.assertEqual([0, 1, 2], sorted(order.tolist()))

    def test_exact_contrast_family_has_72_columns(self) -> None:
        rows = []
        for user_index, user in enumerate(("a" * 64, "b" * 64)):
            for cell in self.contract["cells"]:
                for head_index, head in enumerate(("RANDOM_EXPECTATION",) + HEADS):
                    rows.append({
                        "user_key": user,
                        "encoding": cell["encoding"],
                        "k": cell["k"],
                        "head": head,
                        "top2_mean_q": 0.4 + 0.001 * head_index + 0.01 * user_index,
                        "top2_worst_q_loss": 0.6 - 0.001 * head_index + 0.01 * user_index,
                    })
        values, metadata, users = build_contrasts(pd.DataFrame(rows), self.contract)
        self.assertEqual((2, 72), values.shape)
        self.assertEqual(72, len(metadata))
        self.assertEqual(["a" * 64, "b" * 64], users)

    def test_train_masked_cold_rating_is_not_parsed(self) -> None:
        train_user = next(
            user for user in range(1, 10000)
            if old_user_bucket(user) <= 59 and user_role_bucket(user) < 6000
        )
        stage1_user = next(
            user for user in range(1, 10000)
            if old_user_bucket(user) <= 59 and 6000 <= user_role_bucket(user) < 8000
        )
        excluded_user = next(
            user for user in range(1, 10000)
            if old_user_bucket(user) > 59 or user_role_bucket(user) >= 8000
        )
        payload = (
            "userId,movieId,rating,timestamp\n"
            f"{train_user},1,4.0,BAD_TIMESTAMP\n"
            f"{train_user},2,BAD_RATING,BAD_TIMESTAMP\n"
            f"{train_user},999,BAD_RATING,BAD_TIMESTAMP\n"
            f"{stage1_user},1,3.5,BAD_TIMESTAMP\n"
            f"{stage1_user},2,5.0,BAD_TIMESTAMP\n"
            f"{excluded_user},NOT_A_MOVIE,BAD_RATING,BAD_TIMESTAMP\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "ratings.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ratings.csv", payload)
            lookup = np.asarray([-1, 0, 1], dtype=np.int32)
            warm = np.asarray([True, False], dtype=bool)
            train_hist, stage1_hist, warm_count, cold_count, counters = _first_pass(
                archive, "ratings.csv", lookup, warm,
            )
        self.assertEqual(1, int(train_hist[train_user].sum()))
        self.assertEqual(2, int(stage1_hist[stage1_user].sum()))
        self.assertEqual(1, int(warm_count[stage1_user]))
        self.assertEqual(1, int(cold_count[stage1_user]))
        self.assertEqual(0, counters["train_masked_cold_rating_parsed"])
        self.assertEqual(0, counters["timestamp_parsed"])

    def test_cli_has_no_test_stage2_or_final_phase(self) -> None:
        choices = tuple(next(action for action in build_parser()._actions if action.dest == "phase").choices)
        self.assertNotIn("test", choices)
        self.assertNotIn("stage2", choices)
        self.assertNotIn("final", choices)

    def test_validator_rejects_claim_cell_and_path_mutations(self) -> None:
        mutations = []
        claim = copy.deepcopy(self.contract)
        claim["claim_boundary"]["forbidden"].remove("STRICT_COLD_START")
        mutations.append(claim)
        cell = copy.deepcopy(self.contract)
        cell["cells"][0]["k"] = 30
        mutations.append(cell)
        path = copy.deepcopy(self.contract)
        path["allowed_input_artifacts"]["candidate_identity"]["path"] = "outputs/recommendation-evidence/global-time-v1/test.parquet"
        path["forbidden_input_artifacts"] = []
        mutations.append(path)
        for mutated in mutations:
            with self.assertRaises(RuntimeError):
                validate_contract(mutated)


if __name__ == "__main__":
    unittest.main()
