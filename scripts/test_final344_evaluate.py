"""Synthetic evaluation tests. No real experiment labels or predictions are opened."""
import copy
from math import log2
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import final344_evaluate as ev


CFG = {"bootstrap_samples": 20000, "bootstrap_seed": 344, "primary_ci": .9875,
       "min_ci_users": 30, "calibration_min_users": 20, "calibration_min_rows": 40,
       "origin_timestamp": 1672531200, "horizon_days": 180,
       "selection_rule": "fixed_pareto"}


def primary_fixture(n=30):
    selected = {"selected": {"FM": "FM150", "GBT": "GBT60"}}
    rows, outcomes, calibration = [], {}, {}
    for family, recipe in selected["selected"].items():
        for seed in ev.SEEDS:
            name = f"{recipe}_s{seed}"
            outcomes[name] = {"status": "SUCCESS", "resource_status": "PASS"}
            calibration[(name, 10)] = {"a": 0., "b": 1.}
            for uid in reversed(range(n)):
                for group in ("ALL", "C"):
                    rows.append({"uid": uid, "model": name, "cap": 10, "h": 2, "kind": "CANDIDATE",
                        "group": group, "calibrated_mse": 1. if family == "FM" else .8,
                        "ndcg2": .5 if family == "FM" else .7})
    return pd.DataFrame(rows), selected, outcomes, calibration


class ArithmeticTests(unittest.TestCase):
    def test_equal_user_affine_weights_and_constant(self):
        rows = pd.DataFrame([(1, 10, 2., 1.), (2, 20, 2., 5.), (2, 21, 2., 5.), (2, 22, 2., 5.)],
                            columns=["uid", "movie_id", "raw", "rating"])
        fit = ev.affine_by_user(rows, 2, 4)
        self.assertAlmostEqual(fit["a"], 3., delta=1e-12)
        self.assertEqual(fit["b"], 0.)
        self.assertNotEqual(fit["a"], rows.rating.mean())

    def test_affine_line_and_nonnegative_slope(self):
        frame = pd.DataFrame({"uid": [1, 2, 3], "movie_id": [1, 2, 3], "raw": [1., 2., 3.], "rating": [1., 3., 5.]})
        fit = ev.affine_by_user(frame, 3, 3)
        self.assertAlmostEqual(fit["a"], -1.)
        self.assertAlmostEqual(fit["b"], 2.)
        frame["rating"] = [5., 3., 1.]
        fit = ev.affine_by_user(frame, 3, 3)
        self.assertEqual((fit["a"], fit["b"]), (3., 0.))

    def test_calibration_user_row_thresholds(self):
        frame = pd.DataFrame([(u, i, 2., 3.) for u in range(20) for i in (1, 2)],
                             columns=["uid", "movie_id", "raw", "rating"])
        self.assertEqual(ev.affine_by_user(frame)["state"], "CONSTANT")
        self.assertEqual(ev.affine_by_user(frame.iloc[:-1])["state"], "INSUFFICIENT")
        short = pd.concat([frame[frame.uid.lt(19)], pd.DataFrame([(0, 3, 2., 3.), (0, 4, 2., 3.)], columns=frame.columns)])
        self.assertEqual(ev.affine_by_user(short)["state"], "INSUFFICIENT")

    def test_reject_invalid_truth_duplicates_nonfinite(self):
        self.assertFalse(ev.valid_stars([3.2]))
        with self.assertRaises(ValueError): ev.quality([1., 5.], [2., np.nan], [1, 2], 2)
        with self.assertRaises(ValueError): ev.quality([1., 5.], [2., 3.], [1, 1], 2)

    def test_raw_ranking_not_clipped_ties(self):
        q = ev.quality([1., 5.], [7., 8.], [1, 2], 1)
        self.assertEqual(q["stars"], 5.)
        self.assertEqual(ev.quality([5., 1.], [2., 2.], [9, 3], 1)["stars"], 1.)
        errors = ev.error_metrics([1., 5.], [7., 8.], {"a": 0., "b": 1.})
        self.assertEqual(errors["native_mse"], 8.)
        self.assertEqual(errors["raw_mse"], 22.5)
        self.assertEqual(errors["calibrated_mse"], 8.)

    def test_ndcg_exact_hand_formula(self):
        expected = (1/log2(3))/(1+(2.5/4.5)/log2(3))
        self.assertAlmostEqual(ev.quality([5., .5, 3.], [2., 3., 1.], [1, 2, 3], 2)["ndcg"], expected)

    def test_idcg0_and_incomplete_pages(self):
        q = ev.quality([.5, .5], [1., 2.], [1, 2], 2)
        self.assertTrue(np.isnan(q["ndcg"]))
        self.assertEqual(q["stars"], .5)
        short = ev.quality([5.], [2.], [1], 2)
        self.assertEqual(short["returned"], 1)
        self.assertTrue(np.isnan(short["stars"]))

    def test_next_two_is_not_cumulative(self):
        y, p, ids = [5., 4., 3., 2., 1., .5], [6., 5., 4., 3., 2., 1.], list(range(6))
        self.assertEqual([ev.quality(y, p, ids, end, end-2)["stars"] for end in (2, 4, 6)], [4.5, 2.5, .75])
        self.assertEqual(ev.quality(y, p, ids, 4)["stars"], 3.5)
        self.assertEqual(ev.quality(y, p, ids, 6, 4)["both_low"], 1.)

    def test_selection_pareto_tie_tradeoff_and_missing(self):
        small = {"recipe": "FM150", "status": "SUCCESS", "mse": 1., "ndcg2": .7}
        large = {**small, "recipe": "FM300"}
        self.assertEqual(ev.select_budget(small, large)["choice"], "FM150")
        self.assertEqual(ev.select_budget(small, {**large, "mse": .9})["choice"], "FM300")
        self.assertEqual(ev.select_budget(small, {**large, "mse": .9, "ndcg2": .6})["choice"], "FM150")
        self.assertEqual(ev.select_budget(small, {**large, "mse": None})["reason"], "LARGER_UNAVAILABLE")
        self.assertIsNone(ev.select_budget({**small, "status": "TIMEOUT"}, large)["choice"])

    def test_four_formal_contrasts_and_uid_order(self):
        frame, selected, outcomes, calibration = primary_fixture()
        contrasts, paired, effects, decision = ev.primary_statistics(frame, selected, outcomes, calibration, CFG)
        self.assertEqual(len(contrasts), 4)
        self.assertEqual(len(paired), 120)
        self.assertEqual(len(effects), 12)
        self.assertEqual(decision["decision"], "GBT_CONSISTENT_OBSERVED_IMPROVEMENT")
        np.testing.assert_allclose(contrasts.delta, [-.2, .2, -.2, .2], atol=1e-15)
        for _, part in paired.groupby(["group", "metric"]):
            self.assertEqual(part.uid.tolist(), list(range(30)))
        self.assertTrue(contrasts.confidence.eq(.9875).all())

    def test_missing_seed_has_no_formal_mean_or_ci(self):
        frame, selected, outcomes, calibration = primary_fixture()
        outcomes["GBT60_s345"]["status"] = "TIMEOUT"
        frame = frame[frame.model.ne("GBT60_s345")]
        contrasts, paired, effects, decision = ev.primary_statistics(frame, selected, outcomes, calibration, CFG)
        self.assertFalse(decision["complete_paired_seeds"])
        self.assertEqual(decision["common_seeds"], [339, 344])
        self.assertEqual(len(paired), 0)
        self.assertTrue(contrasts.delta.isna().all() and contrasts.ci_low.isna().all())
        self.assertEqual(set(effects.seed), {339, 344})

    def test_primary_calibration_na_stops_formal_auxiliary_na_does_not(self):
        frame, selected, outcomes, calibration = primary_fixture()
        calibration[("GBT60_s345", 0)] = {"a": None}
        self.assertTrue(ev.primary_statistics(frame, selected, outcomes, calibration, CFG)[3]["complete_paired_seeds"])
        calibration[("GBT60_s345", 10)] = {"a": None}
        self.assertFalse(ev.primary_statistics(frame, selected, outcomes, calibration, CFG)[3]["complete_paired_seeds"])

    def test_c_small_denom_does_not_relax_all_confidence(self):
        frame, selected, outcomes, calibration = primary_fixture()
        frame.loc[frame.uid.eq(29) & frame.group.eq("C"), "ndcg2"] = np.nan
        contrasts, _, _, decision = ev.primary_statistics(frame, selected, outcomes, calibration, CFG)
        self.assertEqual(contrasts.users.tolist(), [30, 30, 30, 29])
        self.assertTrue(np.isnan(contrasts.iloc[-1].ci_low))
        self.assertTrue(contrasts.confidence.eq(.9875).all())
        self.assertEqual(decision["decision"], "NO_WINNER_INCOMPLETE_OR_INSUFFICIENT")

    def test_model_dependent_denom_rejected(self):
        frame, selected, outcomes, calibration = primary_fixture()
        frame.loc[frame.uid.eq(29) & frame.group.eq("C") & frame.model.eq("GBT60_s345"), "ndcg2"] = np.nan
        with self.assertRaises(ValueError): ev.primary_statistics(frame, selected, outcomes, calibration, CFG)

    def test_metric_seed_mean_not_ensemble(self):
        rows = []
        for family, recipe in (("FM", "FM150"), ("GBT", "GBT60")):
            for seed, mse in zip(ev.SEEDS, (4., 0., 4.)):
                rows.append({"uid": 1, "cap": 10, "h": 1, "pre_all": 1, "actual_h": "1_4", "activity": "1_9",
                    "kind": "CANDIDATE", "recipe": recipe, "family": family, "seed": seed,
                    "model": f"{recipe}_s{seed}", "group": "ALL", "targets": 1, "predicted": 1, "native_mse": mse})
        answer = ev.average_seed_metrics(pd.DataFrame(rows))
        np.testing.assert_allclose(answer.native_mse, [8/3, 8/3])
        self.assertFalse(answer.native_mse.eq(0).any())

    def test_seed_effect_reversal_is_disclosed(self):
        frame, selected, outcomes, calibration = primary_fixture()
        for seed, mse in zip(ev.SEEDS, (1.1, .5, .5)):
            frame.loc[frame.model.eq(f"GBT60_s{seed}"), "calibrated_mse"] = mse
        contrasts = ev.primary_statistics(frame, selected, outcomes, calibration, CFG)[0]
        self.assertFalse(contrasts[contrasts.metric.eq("calibrated_mse")].seed_direction_consistent.any())


class OrchestrationTests(unittest.TestCase):
    def test_final_metrics_personalization_and_seed_summaries(self):
        catalog = pd.DataFrame({"movie_id": [1, 2, 3, 4], "blocked": [False, True, False, False],
                                "train_count": [5, 0, 0, 2]})
        contexts, offset = [], 0
        for uid, target in ((91, [0, 1]), (92, [0, 2])):
            for cap in (0, 10):
                contexts.append({"uid": uid, "cap": cap, "h": int(cap > 0), "pre_all": 1,
                    "als_supported_inputs": int(cap > 0), "oi": [3] if cap else [],
                    "ei": target, "start": offset, "stop": offset+2})
                offset += 2
        labels = pd.DataFrame([(91, 1, 5.), (91, 2, 1.), (92, 1, 4.), (92, 3, .5)],
            columns=["uid", "movie_id", "rating"]).set_index(["uid", "movie_id"]).rating
        predictions, calibration = {}, {}
        for recipe in ("FM150", "GBT60"):
            for seed in ev.SEEDS:
                predictions[f"{recipe}_s{seed}"] = np.array([1., 2., 5., 1., 1., 2., 4., .5])
        direct = np.array([False, False, True, False, False, False, True, False])
        predictions["REFERENCE_FM_RH"] = predictions["FM150_s339"].copy()
        predictions["REFERENCE_GBT_B"] = predictions["FM150_s339"].copy()
        predictions["REFERENCE_ALS"] = np.where(direct, predictions["FM150_s339"], np.nan)
        for model in predictions:
            for cap in (0, 10): calibration[(model, cap)] = {"a": 0., "b": 1.}
        masks = {"ALL": np.ones(4, bool), "C": np.array([False, True, False, False]),
                 "NATURAL_ZERO": np.array([False, False, True, False])}
        info = catalog.rename(columns={"train_count": "support"}).assign(release_year=2000, tmdb_vote_count=100,
                                                                        new_metadata_category=False, MISSING_genre_ids=False)
        users, pages, errors, profiles = ev.build_metrics(catalog, contexts, {91: "comparison", 92: "comparison"},
            labels, predictions, calibration, direct, masks, info)
        self.assertEqual(len(profiles), 4)
        self.assertEqual(set(users[users.model.eq("REFERENCE_ALS")].group), {"W_DIRECT"})
        summary, page_summary = ev.summarize_users(users, pages)
        c = summary[summary.model.eq("FM150_s339") & summary.cap.eq(10) & summary.group.eq("C") &
                    summary.slice_type.eq("population") & summary["slice"].eq("H_POSITIVE") & summary.cohort.eq("natural")]
        self.assertEqual(int(c[c.metric.eq("native_mse")].valid.iloc[0]), 1)
        self.assertEqual(int(c[c.metric.eq("ndcg2")].valid.iloc[0]), 0)
        movies, bins, crosses = ev.summarize_movies_and_bins(errors)
        self.assertFalse(movies.empty or bins.empty or crosses.empty or page_summary.empty)
        personal, personal_summary = ev.personalization(users)
        p = personal[personal.model.eq("FM150_s339") & personal.group.eq("ALL") & personal.metric.eq("native_mse")]
        self.assertEqual(len(p), 2)
        self.assertTrue(p.delta.lt(0).all())
        self.assertTrue(personal[personal.metric.eq("calibrated_mse")].includes_different_cap_calibrator.all())
        self.assertFalse(personal_summary.empty)
        mean_users = ev.average_seed_metrics(users)
        mean_pages = ev.average_seed_metrics(pages, page=True)
        self.assertEqual(set(mean_users.model), {"SEED_MEAN_FM", "SEED_MEAN_GBT"})
        self.assertEqual(len(mean_users), len(users[users.model.eq("FM150_s339")])*2)
        self.assertFalse(ev.summarize_users(mean_users, mean_pages)[0].empty)

    def make_api(self, failed=None):
        root = Path("/SYNTHETIC_ONLY")
        a = SimpleNamespace(ROOT=root, BASE=root/"base", OLD=root/"old", OUT=root/"out",
                            FOUND=root/"found", COMBO=root/"combo", LEGACY=root/"legacy", DOC=root/"doc")
        store, sealed, writes = {}, {}, {}
        def pin(path):
            if path.name == "labels.parquet": return {"sha256": ev.LABEL_SHA, "bytes": 1}
            if path.name == "roles.csv": return {"sha256": "ROLES", "bytes": 1}
            return {"sha256": str(path), "bytes": 1}
        a.pin = pin
        a.read = lambda path: store[path]
        a.write_json = lambda path, value: store.__setitem__(path, copy.deepcopy(value))
        a.reviewed = lambda stage: None
        a.lock = lambda: None
        a.fingerprint = lambda stage: {"synthetic": stage}
        a.verify = lambda name: sealed[name]
        a.seal = lambda name, files, **extras: sealed.__setitem__(name, {"files": {f: pin(a.OUT/f) for f in files}, **extras})
        store[a.DOC/"execution.json"] = {"evaluation_scope": "DEVELOPMENT_ONLY"}
        store[a.ROOT/"docs/recommendation/plans/final-fm-gbt/config.json"] = CFG
        store[a.OUT/"model-audit-seal.json"] = {"status": "PASS", "fit_seals": {}}
        for fit_id in ev.INITIAL:
            recipe, seed, _ = ev.parse_fit_id(fit_id)
            store[a.OUT/fit_id/"outcome.json"] = {"fit_id": fit_id, "recipe": recipe, "seed": seed,
                "status": "TIMEOUT" if fit_id == failed else "SUCCESS", "resource_status": "UNKNOWN" if fit_id == failed else "PASS"}
            store[a.OUT/fit_id/"checks.json"] = {"partition_equal": True, "params_equal": True}
            sealed[fit_id+"-seal.json"] = {"files": {}}
            store[a.OUT/"model-audit-seal.json"]["fit_seals"][fit_id] = pin(a.OUT/(fit_id+"-seal.json"))
        return a, store, sealed, writes

    def test_unproven_confirmation_rejected_before_labels(self):
        a, store, _, _ = self.make_api()
        store[a.DOC/"execution.json"]["evaluation_scope"] = "NEW_TEST"
        with patch.object(ev, "api", return_value=a), patch.object(Path, "exists", return_value=False), \
             patch.object(pd, "read_parquet", side_effect=AssertionError("No labels may be read")):
            with self.assertRaises(ValueError): ev.gate(ev.INITIAL)

    def test_unreviewed_fit_rejected_before_labels(self):
        a, store, _, _ = self.make_api()
        store[a.OUT/"model-audit-seal.json"]["fit_seals"].pop("GBT60_s339")
        with patch.object(ev, "api", return_value=a), patch.object(Path, "exists", return_value=False), \
             patch.object(pd, "read_parquet", side_effect=AssertionError("No labels may be read")):
            with self.assertRaises(ValueError): ev.gate(ev.INITIAL)

    def calibration_data(self):
        catalog = pd.DataFrame({"movie_id": [1, 2], "blocked": [False, True], "train_count": [5, 0]})
        contexts = []
        offset = 0
        for uid in range(1, 271):
            for cap in ev.CAPS:
                contexts.append({"uid": uid, "cap": cap, "h": int(cap > 0), "ei": [0, 1], "start": offset, "stop": offset+2})
                offset += 2
        roles = {uid: "calibration" if uid <= 90 else "comparison" for uid in range(1, 271)}
        labels = pd.DataFrame([(u, m, r) for u in roles for m, r in [(1, 5.), (2, 1.)]],
                              columns=["uid", "movie_id", "rating"]).set_index(["uid", "movie_id"]).rating
        models = set(ev.INITIAL) | {f"{r}_s{s}" for r in ("FM150", "GBT60") for s in ev.SEEDS}
        vectors = {model: np.tile([5., 1.], len(contexts)) for model in models}
        return catalog, contexts, roles, labels, vectors

    def test_initial_calibration_and_selection_in_memory(self):
        a, store, sealed, writes = self.make_api()
        catalog, contexts, roles, labels, all_vectors = self.calibration_data()
        vectors = {k: all_vectors[k] for k in ev.INITIAL}
        def save(frame, path, index=False): writes[path] = frame.copy()
        with patch.object(ev, "api", return_value=a), patch.object(Path, "exists", side_effect=lambda p: False, autospec=True), \
             patch.object(ev, "load_data", return_value=(catalog, contexts, roles, labels, vectors)), \
             patch.object(pd.DataFrame, "to_parquet", save):
            ev.calibrate()
            ev.select()
        selected = store[a.OUT/"selection.json"]
        self.assertEqual(selected["status"], "PASS")
        self.assertEqual(selected["selected"], {"FM": "FM150", "GBT": "GBT60"})
        self.assertIn("selection-calibration-seal.json", sealed)
        self.assertIn("selection-seal.json", sealed)
        self.assertEqual(len(writes[a.OUT/"selection-user-metrics.parquet"]), 1440)

    def test_select_rejects_replaced_calibration_parent_after_using_old_coefficients(self):
        # Reproduce the independent review: same evaluation_inputs, but the
        # calibration JSON and its seal both change when selection is written.
        a, store, sealed, writes = self.make_api()
        data = list(self.calibration_data())
        data[-1] = {k: data[-1][k] for k in ev.INITIAL}
        original_pin, original_write = a.pin, a.write_json
        version = {"value": "BEFORE"}
        target = a.OUT/"selection-calibration-seal.json"
        a.pin = lambda path: {"sha256": version["value"], "bytes": 1} if path == target else original_pin(path)
        def mutate_when_selection_is_written(path, value):
            original_write(path, value)
            if path == a.OUT/"selection.json":
                store[a.OUT/"selection-calibration.json"]["fits"][0]["a"] += 1
                sealed["selection-calibration-seal.json"] = copy.deepcopy(sealed["selection-calibration-seal.json"])
                version["value"] = "AFTER"
        a.write_json = mutate_when_selection_is_written
        with patch.object(ev, "api", return_value=a), patch.object(Path, "exists", return_value=False), \
             patch.object(ev, "load_data", return_value=tuple(data)), \
             patch.object(pd.DataFrame, "to_parquet", lambda frame, path, index=False: writes.__setitem__(path, frame.copy())):
            ev.calibrate()
            with self.assertRaisesRegex(ValueError, "frozen parent drift: selection-calibration-seal.json"):
                ev.select()
        self.assertNotIn("selection-seal.json", sealed)

    def test_final_calibrate_rejects_changed_selection_parent(self):
        a, store, sealed, _ = self.make_api()
        data = list(self.calibration_data())
        fit_ids = [f"{r}_s{s}" for r in ("FM150", "GBT60") for s in ev.SEEDS]
        data[-1] = {k: data[-1][k] for k in fit_ids}
        store[a.OUT/"selection.json"] = {"status": "PASS", "selected": {"FM": "FM150", "GBT": "GBT60"}}
        sealed["selection-seal.json"] = {"files": {}}
        outcomes = {k: {"status": "SUCCESS", "resource_status": "PASS"} for k in fit_ids}
        original_pin, original_write = a.pin, a.write_json
        version = {"value": "BEFORE"}
        target = a.OUT/"selection-seal.json"
        a.pin = lambda path: {"sha256": version["value"], "bytes": 1} if path == target else original_pin(path)
        def mutate_when_calibration_is_written(path, value):
            original_write(path, value)
            if path == a.OUT/"final-calibration.json": version["value"] = "AFTER"
        a.write_json = mutate_when_calibration_is_written
        with patch.object(ev, "api", return_value=a), patch.object(Path, "exists", return_value=False), \
             patch.object(ev, "gate", return_value=({"execution": {}}, outcomes)), \
             patch.object(ev, "load_data", return_value=tuple(data)):
            with self.assertRaisesRegex(ValueError, "frozen parent drift: selection-seal.json"):
                ev.calibrate(final=True)
        self.assertNotIn("final-calibration-seal.json", sealed)

    def test_parent_check_also_verifies_children_with_same_seal_pin(self):
        a, _, _, _ = self.make_api()
        expected = a.pin(a.OUT/"final-calibration-seal.json")
        def reject_changed_child(name): raise ValueError("changed calibration JSON")
        a.verify = reject_changed_child
        with patch.object(ev, "api", return_value=a):
            with self.assertRaisesRegex(ValueError, "changed calibration JSON"):
                ev.verify_parent_snapshot("final-calibration-seal.json", expected)

    def test_evaluate_rejects_changed_calibration_or_selection_parent(self):
        for target_name in ("final-calibration-seal.json", "selection-seal.json"):
            with self.subTest(target=target_name):
                a, store, sealed, _ = self.make_api()
                selected = {"status": "PASS", "selected": {"FM": "FM150", "GBT": "GBT60"}}
                store[a.OUT/"selection.json"] = selected
                sealed["selection-seal.json"] = {"files": {}}
                sealed["final-calibration-seal.json"] = {"files": {}}
                fit_ids = [f"{r}_s{s}" for r in ("FM150", "GBT60") for s in ev.SEEDS]
                outcomes = {k: {"status": "SUCCESS", "resource_status": "PASS"} for k in fit_ids}
                original_pin, original_write = a.pin, a.write_json
                version = {"value": "BEFORE"}
                target = a.OUT/target_name
                a.pin = lambda path: {"sha256": version["value"], "bytes": 1} if path == target else original_pin(path)
                parent = {"evaluation_inputs": {"execution": {}}, "selection_seal": a.pin(a.OUT/"selection-seal.json")}
                def mutate_when_decision_is_written(path, value):
                    original_write(path, value)
                    if path == a.OUT/"decision.json": version["value"] = "AFTER"
                a.write_json = mutate_when_decision_is_written
                empty = pd.DataFrame(columns=["dummy"])
                data = (empty, [{"stop": 0}], {}, pd.Series(dtype=float), {})
                decision = {"complete_paired_seeds": False, "decision": "INCOMPLETE"}
                with patch.object(ev, "api", return_value=a), patch.object(Path, "exists", return_value=False), \
                     patch.object(ev, "gate", return_value=({"execution": {}}, outcomes)), \
                     patch.object(ev, "calibration_map", return_value=({}, parent)), \
                     patch.object(ev, "load_data", return_value=data), \
                     patch.object(ev, "references", return_value=({}, np.array([], bool), {}, {})), \
                     patch.object(ev, "metadata_masks", return_value=({}, empty)), \
                     patch.object(ev, "build_metrics", return_value=(empty, empty, empty, empty)), \
                     patch.object(ev, "summarize_users", return_value=(empty, empty)), \
                     patch.object(ev, "summarize_movies_and_bins", return_value=(empty, empty, empty)), \
                     patch.object(ev, "personalization", return_value=(empty, empty)), \
                     patch.object(ev, "primary_statistics", return_value=(empty, empty, empty, decision)), \
                     patch.object(pd.DataFrame, "to_parquet", lambda *args, **kwargs: None), \
                     patch.object(pd.DataFrame, "to_csv", lambda *args, **kwargs: None):
                    with self.assertRaisesRegex(ValueError, "frozen parent drift: " + target_name):
                        ev.evaluate()
                self.assertNotIn("evaluation-seal.json", sealed)

    def test_selected_bad_family_and_incomplete_stop(self):
        a, store, sealed, _ = self.make_api()
        sealed["selection-seal.json"] = {}
        store[a.OUT/"selection.json"] = {"status": "INCOMPLETE", "selected": {"FM": None, "GBT": "GBT60"}}
        with patch.object(ev, "api", return_value=a):
            with self.assertRaises(ValueError): ev.selected_fit_ids()
        store[a.OUT/"selection.json"] = {"status": "PASS", "selected": {"FM": "GBT60", "GBT": "FM150"}}
        with patch.object(ev, "api", return_value=a):
            with self.assertRaises(ValueError): ev.selected_fit_ids()


if __name__ == "__main__":
    unittest.main(verbosity=2)
