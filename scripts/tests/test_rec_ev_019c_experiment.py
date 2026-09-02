from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_bounded_core import BudgetLedger
from rec_ev_019c_data import PreparedInputs
from rec_ev_019c_experiment import ExperimentEngine
from rec_ev_019c_lightfm import LightfmRepresentations


class RecEv019CExperimentTest(unittest.TestCase):
    def test_tiny_suite_runs_all_heads_without_opening_files(self) -> None:
        contract = copy.deepcopy(json.loads(
            (ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json").read_text(encoding="utf-8")
        ))
        contract["source_preconditions"]["validation_strict_users_by_k"] = {"0": 1, "5": 1, "10": 1}
        contract["resource_execution_plan"]["tuning_panel"]["users_per_k"] = {"0": 1, "5": 1, "10": 1}
        contract["models"]["B4_BPR_MF"]["fixed_parameters"]["epochs"] = 1
        contract["models"]["B8_LIGHTFM"]["fixed_parameters"]["epochs"] = 1
        candidate_ids = np.arange(1, 13, dtype=np.int64)
        base_binary = sparse.csr_matrix(np.asarray([
            [1, -1, 1, -1, 0, 0, 0, 0, 0, 0, 0, 0],
            [1, -1, 0, 0, 1, -1, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, -1, 1, -1, 0, 0, 0, 0, 0, 0],
        ], dtype=np.int8))
        prefix_rows = []
        labels = [1, -1] * 5
        for k in (5, 10):
            for rank in range(k):
                prefix_rows.append({
                    "user_key": "v", "k": k, "input_rank": rank + 1,
                    "movie_id": rank + 1, "binary_label": labels[rank],
                })
        window_rows = []
        for k in (0, 5, 10):
            for rank, (movie_id, utility, positive, negative) in enumerate(
                [(11, 0.9, True, False), (12, 0.1, False, True)], start=1
            ):
                window_rows.append({
                    "user_key": "v", "k": k, "window_rank": rank, "movie_id": movie_id,
                    "midrank_utility": utility, "is_positive": positive, "is_negative": negative,
                })
        features = sparse.csr_matrix(np.eye(12, 4, dtype=np.float32))
        inputs = PreparedInputs(
            candidate_core=pd.DataFrame({"movie_id": candidate_ids, "tmdb_id": candidate_ids}),
            candidate_ids=candidate_ids,
            movie_position={int(movie_id): index for index, movie_id in enumerate(candidate_ids)},
            b0_rating_count=np.full(12, 2, dtype=np.int64),
            b0_rating_mean=np.linspace(1.0, 5.0, 12),
            base_user_keys=np.asarray(["a", "b", "c"]),
            base_binary=base_binary,
            validation_prefixes=pd.DataFrame(prefix_rows),
            validation_windows=pd.DataFrame(window_rows),
            structured_by_variant={
                "FULL": features, "DROP_KEYWORDS": features,
                "DROP_PEOPLE": features, "CORE_ONLY_GENRE_LANGUAGE_DECADE_RUNTIME": features,
            },
            structured_available=np.ones(12, dtype=bool),
            text_embeddings=np.pad(np.eye(12, dtype=np.float32), ((0, 0), (0, 372))),
            text_available=np.ones(12, dtype=bool),
            input_checksums={},
        )

        def fake_lightfm(_contract, _interactions, _features, parameters, seed, _path):
            rng = np.random.default_rng(seed)
            dimension = int(parameters["dimension"])
            return LightfmRepresentations(
                np.zeros(12, dtype=np.float32),
                rng.normal(size=(12, dimension)).astype(np.float32),
            )

        ledger = BudgetLedger(contract["resource_execution_plan"]["budgets"])
        predictions = []
        with tempfile.TemporaryDirectory() as directory:
            engine = ExperimentEngine(
                contract, inputs, ledger, lightfm_fit=fake_lightfm, cache_root=Path(directory)
            )
            tuning = engine.run_tuning()
            metrics = engine.run_full_validation(
                lambda model, k, trial, user, rows: predictions.append((model, k, trial, user, len(rows)))
            )
        self.assertEqual(set(contract["models"]), set(tuning.per_model_per_k))
        self.assertEqual(15, len(metrics))
        self.assertEqual(15, len(predictions))
        self.assertGreater(len(tuning.trial_user_metrics), 0)


if __name__ == "__main__":
    unittest.main()
