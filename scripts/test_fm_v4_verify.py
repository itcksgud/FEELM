from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fm_v4_train import validate_prepared_contract
from fm_v4_verify import scan_episode_file, verify_tuning


def episode(n: int, role: str = "TRAIN") -> dict:
    history = [
        {"event_id": "100:1", "movie_id": 1, "rating": 3.0, "event_at": 100},
        {"event_id": "200:2", "movie_id": 2, "rating": 4.0, "event_at": 200},
    ][-n:] if n else []
    key = f"{role}:7:300:3"
    row = {
        "role": role, "uid": 7, "target_key": key, "episode_id": f"{key}:{n}",
        "target_movie_id": 3, "target_event_at": 300, "prediction_at": 300,
        "n": n, "n_bucket": str(n), "total_history_count": 2,
        "supported_history_count": n, "history_cap": 50, "history": history,
    }
    if role == "TRAIN":
        row["target_rating"] = 4.5
    return row


def trace(value: float, best: float) -> dict:
    return {
        "best_epoch": 1, "best_selection_positive_n_user_macro_mse": best,
        "epochs_executed": 1, "returned_epoch": 1,
        "history": [
            {"epoch": 0, "selection_positive_n_user_macro_mse": value,
             "fit_weighted_mse": value},
            {"epoch": 1, "selection_positive_n_user_macro_mse": best,
             "fit_weighted_mse": best},
        ],
    }


def model_config() -> dict:
    return {"ridge_reg_grid": [0.0001, 0.001], "factor_reg_grid": [0.001, 0.01],
            "model_seeds": [623, 1623, 2623], "optimizer": {"max_epochs": 80}}


def tuning_document() -> dict:
    trials = []
    for reg, value in ((0.001, 0.9), (0.01, 1.0)):
        seed_trials = [{"seed": seed, "trace": trace(value + index * 0.01, value - 0.1 + index * 0.01)}
                       for index, seed in enumerate((623, 1623, 2623))]
        trials.append({
            "reg": reg, "seed_trials": seed_trials,
            "mean_holdout_positive_n_user_macro_mse": sum(
                item["trace"]["best_selection_positive_n_user_macro_mse"] for item in seed_trials
            ) / 3,
        })
    selected = trials[0]
    epochs = {str(item["seed"]): 1 for item in selected["seed_trials"]}
    return {
        "model_contract": model_config(),
        "selection_metric": "TRAIN_INNER_HOLDOUT_POSITIVE_N_HIERARCHICAL_USER_MACRO_MSE",
        "validation_labels_read": False, "rank": 4,
        "additive_parameters_frozen_and_shared_bitwise": True,
        "internal_fit_rows": 2, "internal_holdout_rows": 1,
        "ridge_trials": [
            {"reg": 0.0001, "holdout_positive_n_user_macro_mse": 0.8},
            {"reg": 0.001, "holdout_positive_n_user_macro_mse": 0.9},
        ],
        "selected_ridge_reg": 0.0001,
        "interaction_trials": trials,
        "selected_interaction_reg": selected["reg"],
        "selected_interaction_mean_holdout_positive_n_user_macro_mse":
            selected["mean_holdout_positive_n_user_macro_mse"],
        "selected_epochs_by_seed": epochs,
        "final_interaction_refits": [
            {"seed": seed, "selected_epoch": 1,
             "trace": {"epochs_executed": 1, "returned_epoch": 1,
                       "history": [{"epoch": 0}, {"epoch": 1}]}}
            for seed in (623, 1623, 2623)
        ],
        "train_only_mechanism_diagnostics": {
            "status": "EXCLUDED_FROM_EXECUTABLE_PROTOCOL", "profiles": [],
            "reason": "only the frozen primary comparison is executable; diagnostics cannot override it",
        },
    }


class FMV4VerifierTests(unittest.TestCase):
    def test_trainer_requires_prepared_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "manifest is required"):
                validate_prepared_contract(Path(temporary), {"pins": {}})

    def test_episode_scan_recomputes_recent_n_and_rejects_missing_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "train.jsonl"
            path.write_text("".join(json.dumps(episode(n), sort_keys=True, separators=(",", ":")) + "\n"
                                    for n in (0, 1, 2)), encoding="utf-8")
            result = scan_episode_file(path, "TRAIN")
            self.assertEqual(result["active_targets"], 1)
            self.assertEqual(result["file"]["rows"], 3)
            path.write_text("".join(json.dumps(episode(n), sort_keys=True, separators=(",", ":")) + "\n"
                                    for n in (0, 1)), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "wrong recent-N"):
                scan_episode_file(path, "TRAIN")

    def test_episode_scan_enforces_validation_label_firewall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "validation.jsonl"
            rows = [episode(n, "VALIDATION") for n in (0, 1, 2)]
            rows[1]["target_rating"] = 5.0
            path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                                    for row in rows), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "target-rating boundary"):
                scan_episode_file(path, "VALIDATION")

    def test_tuning_lineage_rejects_grid_or_seed_tampering(self) -> None:
        config = {"model": model_config()}
        train = pd.DataFrame({"internal_split": ["FIT", "FIT", "HOLDOUT"]})
        checks = []
        verify_tuning(tuning_document(), config, train, checks)
        self.assertTrue(checks)
        changed = copy.deepcopy(tuning_document())
        changed["interaction_trials"][0]["seed_trials"][1]["seed"] = 999
        with self.assertRaisesRegex(RuntimeError, "seed set/order"):
            verify_tuning(changed, config, train, [])
        changed = copy.deepcopy(tuning_document())
        changed["ridge_trials"].reverse()
        with self.assertRaisesRegex(RuntimeError, "ridge grid/order"):
            verify_tuning(changed, config, train, [])


if __name__ == "__main__":
    unittest.main()
