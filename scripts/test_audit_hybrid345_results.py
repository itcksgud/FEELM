from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import audit_hybrid345_results as audit


def synthetic_users(n: int = 32) -> pd.DataFrame:
    rows = []
    models = ("ALS", "ALS_QWEN", "STRUCTURED_DIRECT", "QWEN_DIRECT", "ALS_C2F",
              "FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ROUTER_s339")
    for uid in range(1, n + 1):
        for model in models:
            group = "W_DIRECT" if model in {"ALS", "ALS_QWEN"} else ("ALL" if model == "ROUTER_s339" else "C")
            base = uid / 1000
            row = {"model": model, "uid": uid, "cap": 10, "group": group, "h": 10,
                   "h_group": "H_POSITIVE", "targets_total": 6, "targets_scored": 6,
                   "complete": True, "mse": 0.7 + base, "mae": 0.6 + base,
                   "bias": 0.01, "pa": 0.65, "ndcg1": 0.7 + base,
                   "ndcg2": 0.75 + base, "ndcg4": 0.8 + base, "ndcg6": 0.82 + base,
                   "stars1": 4.0, "stars2": 3.9, "stars4": 3.8, "stars6": 3.7,
                   "good1": 1.0, "good2": 0.8, "good4": 0.7, "good6": 0.6,
                   "low1": 0.0, "low2": 0.1, "low4": 0.15, "low6": 0.2,
                   "any_low1": 0.0, "any_low2": 0.2, "any_low4": 0.3, "any_low6": 0.4,
                   "both_low1": math.nan, "both_low2": 0.0,
                   "both_low4": math.nan, "both_low6": math.nan}
            rows.append(row)
    return pd.DataFrame(rows)


class Hybrid345ResultAuditTests(unittest.TestCase):
    def test_final344_calibration_reference_is_bound_to_input_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_lock_relative = "sealed/input-lock.json"
            input_lock_path = root / input_lock_relative
            audit.write_json_atomic(input_lock_path, {"fixed": True})
            calibration_path = root / audit.FINAL344_CALIBRATION_RELATIVE
            calibration_path.parent.mkdir(parents=True)
            audit.write_json_atomic(calibration_path, {"scope": "DEVELOPMENT_ONLY", "fits": []})
            seal_path = root / audit.FINAL344_CALIBRATION_SEAL_RELATIVE
            audit.write_json_atomic(seal_path, {
                "input_lock": audit.pin(input_lock_path),
                "files": {"final-calibration.json": audit.pin(calibration_path)},
                "execution": {},
            })
            config = {
                "sources": {"final344_input_lock": input_lock_relative},
                "source_pins": {input_lock_relative: audit.pin(input_lock_path)},
            }
            actual = audit.verify_final344_calibration_parent(config, root)
            self.assertEqual(actual["final344_calibration_seal"],
                             (seal_path, audit.pin(seal_path)))
            self.assertEqual(actual["final344_calibration"],
                             (calibration_path, audit.pin(calibration_path)))
            with seal_path.open("ab") as handle:
                handle.write(b"tamper")
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                audit.verify_final344_calibration_parent(config, root)

    def test_vote_metadata_follows_locked_rec045_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata_path = root / audit.TMDB_VOTE_METADATA_RELATIVE
            metadata_path.parent.mkdir(parents=True)
            expected = pd.DataFrame({"movie_id": [1, 2], "tmdb_vote_count": [0.0, 99.0]})
            expected.to_parquet(metadata_path, index=False)
            input_lock_relative = "sealed/input-lock.json"
            input_lock_path = root / input_lock_relative
            audit.write_json_atomic(input_lock_path, {
                "evaluation_scope": "DEVELOPMENT_ONLY",
                "evaluation_labels_read": False,
                "files": {audit.TMDB_VOTE_METADATA_RELATIVE: audit.pin(metadata_path)},
            })
            config = {
                "sources": {"final344_input_lock": input_lock_relative},
                "source_pins": {input_lock_relative: audit.pin(input_lock_path)},
            }
            actual, inputs = audit.load_locked_tmdb_vote_metadata(
                config, pd.DataFrame({"movie_id": [1, 2]}), root)
            pd.testing.assert_frame_equal(actual, expected)
            self.assertEqual(inputs["tmdb_vote_metadata"], audit.pin(metadata_path))

            broken = expected.assign(tmdb_vote_count=[0.5, 99.0])
            broken.to_parquet(metadata_path, index=False)
            audit.write_json_atomic(input_lock_path, {
                "evaluation_scope": "DEVELOPMENT_ONLY",
                "evaluation_labels_read": False,
                "files": {audit.TMDB_VOTE_METADATA_RELATIVE: audit.pin(metadata_path)},
            })
            config["source_pins"][input_lock_relative] = audit.pin(input_lock_path)
            with self.assertRaisesRegex(ValueError, "finite nonnegative integers"):
                audit.load_locked_tmdb_vote_metadata(config, None, root)

    def test_row_errors_to_user_page_summary_and_diagnostics_end_to_end(self) -> None:
        ratings = np.array([5.0, 4.0, 3.0, 2.0, 1.0, 3.5])
        movie_ids = np.arange(101, 107)
        rows = []
        for model_index, model in enumerate((*audit.BASE_MODELS, *audit.DERIVED_MODELS)):
            ranking = np.linspace(0.9, 0.1, 6) + model_index / 100
            calibrated = ratings + np.array([0.1, -0.1, 0.2, 0.0, 0.1, -0.2])
            for index in range(6):
                error = calibrated[index] - ratings[index]
                rows.append({"model": model, "context_id": 7, "row_id": index, "uid": 1,
                             "cap": 10, "h": 6, "movie_id": movie_ids[index],
                             "rating": ratings[index], "available": True,
                             "ranking_score": ranking[index], "calibrated_raw": calibrated[index],
                             "calibrated": calibrated[index], "error": error, "se": error ** 2,
                             "ae": abs(error), "primary_group": "W_DIRECT" if index < 2 else
                             ("C" if index < 4 else "NATURAL_ZERO"),
                             "cold_partition": "V" if index == 2 else ("E" if index == 3 else ""),
                             "support_band": ("50_PLUS", "10_49", "0", "0", "0", "0")[index],
                             "release_band": "2020_2022", "vote_band": "50_499"})
        errors = pd.DataFrame(rows)
        users, pages = audit.recompute_from_row_errors(errors)
        audit.compare_metric_table(users, users.copy())
        audit.compare_metric_table(pages, pages.copy(), page=True)
        page_summary = audit.summarize_pages_independent(pages)
        audit.compare_summary_like(page_summary, page_summary.copy(),
                                   ["model", "cap", "group", "h_group", "start", "end", "metric"])
        diagnostics = audit.recompute_diagnostics(errors)
        audit.compare_diagnostics(diagnostics, diagnostics.copy())
        broken = users.copy()
        broken.loc[0, "mse"] += 0.01
        with self.assertRaisesRegex(ValueError, "mse"):
            audit.compare_metric_table(users, broken)

    def test_selection_metrics_reproduce_frozen_choices(self) -> None:
        config = audit.read_json(audit.CONFIG)
        rows = []
        warm_values = {
            "0.0": (0.70, 0.80, 4.0, 0.10), "0.1": (0.69, 0.81, 4.0, 0.10),
            "0.25": (0.71, 0.82, 4.0, 0.10), "0.5": (0.72, 0.79, 3.7, 0.20),
        }
        cold_values = {
            "GBT120_s339": (0.75, 0.78, 3.8, 0.12), "STRUCTURED_DIRECT": (0.80, 0.76, 3.8, 0.12),
            "QWEN_DIRECT": (0.70, 0.82, 3.8, 0.12), "ALS_C2F": (0.72, 0.80, 3.8, 0.12),
            "FM150_s339": (0.77, 0.79, 3.8, 0.12),
        }
        for uid in range(1, 33):
            for candidate, values in warm_values.items():
                rows.append({"selection": "WARM_WEIGHT", "candidate": candidate, "uid": uid,
                             "cap": 10, "group": "W_DIRECT", "h": 10, "complete": True,
                             "common_selection_uid": True, "mse": values[0], "ndcg2": values[1],
                             "stars2": values[2], "low2": values[3], "any_low2": values[3],
                             "weight": float(candidate)})
            for candidate, values in cold_values.items():
                rows.append({"selection": "COLD_HEAD", "candidate": candidate, "uid": uid,
                             "cap": 10, "group": "C", "h": 10, "complete": True,
                             "common_selection_uid": True, "mse": values[0], "ndcg2": values[1],
                             "stars2": values[2], "low2": values[3], "any_low2": values[3],
                             "weight": math.nan})
        frame = pd.DataFrame(rows)
        # The producer's selection artifact is already scoped to the frozen cap and
        # movie group, so those two constants are intentionally not persisted.
        frame = frame.drop(columns=["cap", "group"])
        def summary(values: tuple[float, float, float, float]) -> dict[str, object]:
            return {"users": 32, "mse": values[0], "mse_users": 32,
                    "ndcg2": values[1], "ndcg2_users": 32, "stars2": values[2],
                    "stars2_users": 32, "low2": values[3], "low2_users": 32,
                    "any_low2": values[3], "any_low2_users": 32}
        selection = {"scope": "DEVELOPMENT_ONLY", "roles_used": ["calibration"],
                     "comparison_labels_used": False, "cap": 10,
                     "warm": {"content_weight": 0.1, "reason": "SMALLEST_POSITIVE_PARETO_SAFE",
                              "baseline": summary(warm_values["0.0"]),
                              "candidates": {name: summary(value) for name, value in warm_values.items()}},
                     "cold": {"head": "QWEN_DIRECT", "reason": "BEST_PARETO_SAFE_CHALLENGER",
                              "incumbent": "GBT120_s339", "eligible_challengers": ["QWEN_DIRECT", "ALS_C2F"],
                              "candidates": {name: summary(cold_values[name]) for name in sorted(cold_values)}},
                     "common_denominator_policy": "ALL_DECLARED_MODELS_SCORE_EVERY_TARGET_OF_THE_USER_GROUP",
                     "service_adoption": False}
        result = audit.verify_selection_metrics(frame, selection, config)
        self.assertEqual(result["warm_weight"], 0.1)
        self.assertEqual(result["cold_head"], "QWEN_DIRECT")
        broken = dict(selection, cold=dict(selection["cold"], head="ALS_C2F"))
        with self.assertRaisesRegex(ValueError, "cold selection"):
            audit.verify_selection_metrics(frame, broken, config)

    def test_gate_failure_occurs_before_any_label_or_result_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(audit, "verify_all_gates", side_effect=ValueError("sealed gate failed")), \
                 patch.object(audit.pd, "read_parquet") as parquet_read:
                with self.assertRaisesRegex(ValueError, "sealed gate failed"):
                    audit.audit(output, output / "RESULT.md", output / "audit.json")
                parquet_read.assert_not_called()

    def test_summary_and_denominator_reaggregation_detects_drift(self) -> None:
        users = synthetic_users()
        summary = audit.recompute_core_summary(users)
        audit.compare_core_summary(summary, summary.copy())
        denominator_rows = []
        for key, frame in users.groupby(["model", "cap", "group", "h_group"], sort=True):
            denominator_rows.append(dict(zip(["model", "cap", "group", "h_group"], key)) | {
                "user_contexts": len(frame), "complete_user_contexts": int(frame.complete.sum()),
                "target_rows": int(frame.targets_total.sum()), "scored_rows": int(frame.targets_scored.sum())})
        denominators = pd.DataFrame(denominator_rows)
        audit.compare_denominators(users, denominators)
        broken = summary.copy()
        broken.loc[0, "valid_users"] += 1
        with self.assertRaisesRegex(ValueError, "valid-user"):
            audit.compare_core_summary(summary, broken)

    def test_bootstrap_is_independent_and_deterministic(self) -> None:
        delta = np.linspace(-0.2, 0.3, 40)
        seed = audit.bootstrap_seed(346, "cold", "ALS_C2F", "QWEN_DIRECT", "mse")
        again = audit.bootstrap_seed(346, "cold", "ALS_C2F", "QWEN_DIRECT", "mse")
        other = audit.bootstrap_seed(346, "cold", "ALS_C2F", "QWEN_DIRECT", "ndcg2")
        self.assertEqual(seed, again)
        self.assertNotEqual(seed, other)
        first = audit.bootstrap_interval(delta, 500, seed, 0.95, 30)
        second = audit.bootstrap_interval(delta, 500, seed, 0.95, 30)
        self.assertEqual(first, second)
        self.assertEqual(audit.bootstrap_interval(delta[:20], 500, seed, 0.95, 30), (None, None))

    def test_exact_fourteen_contrasts_reproduce_uid_pairs_and_intervals(self) -> None:
        config = audit.read_json(audit.CONFIG)
        config["bootstrap"] = dict(config["bootstrap"], samples=300)
        rows = []
        models_by_group = {
            "W_DIRECT": {"ALS", "ALS_QWEN"},
            "C": {"STRUCTURED_DIRECT", "QWEN_DIRECT", "ALS_C2F",
                  "FM150_SEED_MEAN", "GBT120_SEED_MEAN"},
            "ALL": {"ROUTER_s339", "FM150_SEED_MEAN", "GBT120_SEED_MEAN"},
        }
        for group, models in models_by_group.items():
            for uid in range(1, 33):
                for model_index, model in enumerate(sorted(models)):
                    rows.append({"model": model, "uid": uid, "cap": 10, "group": group,
                                 "h": 10, "mse": 0.5 + uid / 1000 + model_index / 100,
                                 "ndcg2": 0.7 + uid / 2000 - model_index / 200})
        users = pd.DataFrame(rows)
        contrasts, pairs = audit.recompute_contrasts(users, config)
        self.assertEqual(len(contrasts), 14)
        self.assertEqual(contrasts.family.value_counts().to_dict(), {"cold": 8, "router": 4, "warm": 2})
        audit.compare_contrasts(contrasts, contrasts.copy(), pairs, pairs.copy())
        broken = contrasts.copy()
        broken.loc[0, "seed"] += 1
        with self.assertRaisesRegex(ValueError, "seed"):
            audit.compare_contrasts(contrasts, broken, pairs, pairs.copy())

    def test_catalog_reaggregation_and_tamper_detection(self) -> None:
        rows = []
        for model in audit.CATALOG_MODELS:
            for uid in (1, 2):
                for rank in range(1, 7):
                    rows.append({"uid": uid, "model": model, "rank": rank,
                                 "movie_id": rank + uid * 100, "unknown": rank % 2 == 0,
                                 "support": (0, 1, 10, 50, 2, 100)[rank - 1],
                                 "actual_als_supported": rank >= 3, "blocked": False})
        top = pd.DataFrame(rows)
        summary = audit.recompute_catalog(top, users=2, eligible_movies=100)
        audit.compare_catalog(summary, summary.copy())
        broken = summary.copy()
        broken.loc[0, "unknown"] += 1
        with self.assertRaisesRegex(ValueError, "unknown"):
            audit.compare_catalog(summary, broken)

    def test_report_scope_and_selection_fail_closed(self) -> None:
        selection = {"warm": {"content_weight": 0.1}, "cold": {"head": "QWEN_DIRECT"}}
        with self.assertRaisesRegex(ValueError, "development-only"):
            audit.verify_report_text("# result", pd.DataFrame(), pd.DataFrame(), selection, pd.DataFrame(),
                                     pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {})

    def test_auditor_does_not_import_evaluator(self) -> None:
        source = (audit.ROOT / "scripts/audit_hybrid345_results.py").read_text(encoding="utf-8")
        self.assertNotIn("import hybrid345_evaluate", source)
        self.assertNotIn("from hybrid345_evaluate", source)


if __name__ == "__main__":
    unittest.main()
