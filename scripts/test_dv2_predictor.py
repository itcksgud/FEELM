"""Reproducible predictor review without trained-model prediction or label reads.

Default: synthetic metadata, fake GBT and fake ALS factors, plus parity against
the independently vectorized frozen RH formula. --source-audit additionally
loads/verifies trained weights without calling their predict method.
--feature-parity compares 109 real feature rows against the sealed RH230 score
matrix, projecting ONLY row_id and x000..x229 (never the label column).

Example (all checks; no outputs written):
  python -B scripts/test_dv2_predictor.py --reference-root C:/higher/projects/FEELM-standalone --source-audit --feature-parity
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

import dv2_predictor as d

REFERENCE_ROOT = Path("C:/higher/projects/FEELM-standalone")
SOURCE_AUDIT = False
FEATURE_PARITY = False
BATCH_CODE_SHA256 = "83e2586d9acc8bfb8aec8dc47b8133f4bc0da8678ba0d82bdd295b5c7bf13cf9"
EVIDENCE = {"trained_model_predictions": 0, "future_label_columns_read": 0}


def movie(service_id, **changes):
    row = dict(service_movie_id=service_id, genre_ids=[18], keyword_ids=[41, 42],
               production_country_codes=["KR"], original_language="ko",
               director_ids=[900001], top5_cast_ids=[900011, 900012],
               production_company_ids=[123], collection_ids=[], release_year=2020,
               runtime_minutes=100, tmdb_vote_average=7., tmdb_vote_count=20,
               tmdb_popularity=3., mapping_status="UNMATCHED", movielens_movie_id=None)
    row.update(changes)
    return row


class SpyGBT:
    """A synthetic feature-sensitive regressor, never the trained GBT."""
    def __init__(self):
        self.calls = []

    def predict(self, x):
        self.calls.append(x.copy())
        return 2. + x[:, 0].astype(float) + x[:, 164].astype(float)


def fixture():
    adapter, _ = d.reference_classes(REFERENCE_ROOT)
    frame = pd.DataFrame([movie(10 + i, genre_ids=[18, 35] if i % 2 else [18],
                               director_ids=[900 + i % 3], tmdb_vote_count=i + 1)
                          for i in range(34)])
    frame.loc[:2, "mapping_status"] = "MATCHED"
    frame.loc[:2, "movielens_movie_id"] = [101, 102, 103]
    names = tuple(adapter(frame.rename(columns={"service_movie_id": "movie_id"})).names)
    fits = {cap: {"ALS": None if cap == 0 else (1 + cap * .01, .5),
                  "GBT120_s339": (.2 + cap * .01, .8)} for cap in d.CAPS}
    spy = SpyGBT()
    assets = d.PredictorAssets(
        adapter, spy, np.array([101, 102, 103]),
        np.array([[1., 0.], [0., 1.], [1., 1.]]),
        np.array([101, 102, 103]), np.array([1, 20, 100]), fits, names,
        {"synthetic": True})
    return frame, assets, spy


def batch_reference(frame, history, stars, candidates):
    """Sealed vectorized formula, supplied a fixed prior without any data fit."""
    code = REFERENCE_ROOT / "scripts/foundation340_features.py"
    if d._pin(code)["sha256"] != BATCH_CODE_SHA256:
        raise ValueError("independent batch reference code drift")
    sys.path.insert(0, str(REFERENCE_ROOT / "scripts"))
    try:
        module = importlib.import_module("foundation340_features")
        if Path(module.__file__).resolve() != code.resolve():
            raise ValueError("batch reference loaded from another location")
        original = importlib.import_module("cold_item_features").Features
        capped = importlib.import_module("text339_relations").CappedRelations
    finally:
        sys.path.pop(0)
    ref = object.__new__(module.Features)
    ref.original = original(frame)
    ref.original.base = capped(frame)
    ref.base = ref.original.base
    ref.shrunk = ref.original.crowd.copy()
    valid = ref.original.valid[:, 0]
    votes = frame.tmdb_vote_count.to_numpy(float)
    ref.shrunk[valid, 0] = (votes[valid] * ref.shrunk[valid, 0] +
                          48 * 6.173088067675869 / 10) / (votes[valid] + 48)
    lookup = {mid: i for i, mid in enumerate(frame.movie_id)}
    n, h = len(candidates), len(history)
    oi = np.tile(np.pad([lookup[mid] for mid in history], (0, 30 - h)), (n, 1)).astype(int)
    ratings = np.tile(np.pad(stars, (0, 30 - h)), (n, 1))
    mask = np.tile(np.arange(30) < h, (n, 1))
    ei = np.array([lookup[mid] for mid in candidates])
    result = ref.batch(oi, ratings, mask, ei)
    result[:, module.RATING_COLUMNS] = ref.crowd(oi, ratings, mask, ei, True)[:,
                                                                              np.array(module.RATING_COLUMNS) - 200]
    return result


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        self.frame, self.assets, self.spy = fixture()
        self.predictor = d.ServicePredictor(self.frame, self.assets)
        self.context = {"cap": 10, "oi": [0], "stars": [4.5], "viewed": [0, 4]}

    def test_four_variants_exact_formula_and_computed_candidate_subsets(self):
        candidates = [1, 2, 3]
        x = self.predictor.features(self.context, candidates)
        gbt = .3 + .8 * (2. + x[:, 0].astype(float) + x[:, 164].astype(float))
        y = self.assets.factors[[0]]
        user = np.linalg.solve(y.T @ y + .1 * np.eye(2), y.T @ np.array([4.5]))
        als = 1.1 + .5 * (self.assets.factors[[1, 2]] @ user)
        for variant in d.VARIANTS:
            with self.subTest(variant=variant):
                self.spy.calls.clear()
                result = self.predictor.predict(self.context, candidates, variant)
                weight = np.array([1., 1., 0.])
                if variant.startswith("shrink"):
                    count = np.array([20, 100, 0])
                    weight *= count / (count + float(variant[6:]))
                expected = gbt.copy()
                expected[:2] = weight[:2] * als + (1 - weight[:2]) * gbt[:2]
                np.testing.assert_allclose(result["prediction"], expected, rtol=0, atol=1e-14)
                np.testing.assert_array_equal(result["als_weight"], weight)
                self.assertEqual(result["als_rows_computed"], 2)
                self.assertEqual(result["gbt_rows_computed"], int((weight < 1).sum()))
                self.assertEqual(len(self.spy.calls), 1)
                np.testing.assert_array_equal(self.spy.calls[0], x[weight < 1])
                backward = self.predictor.predict(self.context, candidates[::-1], variant)
                np.testing.assert_array_equal(backward["prediction"], result["prediction"][::-1])

    def test_min20_uses_fallback_for_low_support_but_preserves_availability(self):
        result = self.predictor.predict({"cap": 1, "oi": [1], "stars": [4.]}, [0, 2, 3], "min20")
        self.assertEqual(result["als_available"].tolist(), [True, True, False])
        self.assertEqual(result["branch"].tolist(), ["GBT", "ALS", "GBT"])
        self.assertEqual((result["als_rows_computed"], result["gbt_rows_computed"]), (1, 2))
        self.assertTrue(np.isnan(result["raw_als"][[0, 2]]).all())

    def test_empty_unavailable_and_warm_only_skip_unneeded_heads(self):
        for context in ({"cap": 0, "oi": [], "stars": []},
                        {"cap": 10, "oi": [4], "stars": [1.]}):
            with self.subTest(context=context):
                result = self.predictor.predict(context, [1, 2, 3])
                self.assertEqual((result["als_rows_computed"], result["gbt_rows_computed"]), (0, 3))
                self.assertTrue(np.isnan(result["raw_als"]).all())
        self.spy.calls.clear()
        empty = self.predictor.predict(self.context, [])
        self.assertEqual(empty["prediction"].shape, (0,))
        self.assertEqual(self.spy.calls, [])
        warm = self.predictor.predict(self.context, [1, 2])
        self.assertEqual(self.spy.calls, [])
        self.assertEqual((warm["als_rows_computed"], warm["gbt_rows_computed"]), (2, 0))

    def test_requested_cap_unclipped_ranking_and_clipped_error_values(self):
        for cap in [1, 5, 10, 30]:
            context = {"cap": cap, "oi": [0], "stars": [5.]}
            result = self.predictor.predict(context, [2, 3])
            a, b = self.assets.calibrations[cap]["ALS"]
            self.assertAlmostEqual(result["prediction"][0], a + b * result["raw_als"][0])
            a, b = self.assets.calibrations[cap]["GBT120_s339"]
            self.assertAlmostEqual(result["prediction"][1], a + b * result["raw_gbt"][1])
        # A synthetic extreme head preserves rank score and clips only the star readout.
        self.assets.calibrations[10]["ALS"] = (10., 1.)
        result = self.predictor.predict(self.context, [1, 2])
        self.assertTrue((result["prediction"] > 5).all())
        np.testing.assert_array_equal(result["rating_clipped"], [5., 5.])

    def test_service_mapping_ambiguity_and_original_factor_ids(self):
        changed = self.frame.copy()
        changed.loc[1, "mapping_status"] = "AMBIGUOUS"
        changed.loc[3, "movielens_movie_id"] = 101  # Service-only mapping is not evidence.
        predictor = d.ServicePredictor(changed, self.assets)
        np.testing.assert_array_equal(predictor.train_count[:4], [1, 0, 100, 0])
        np.testing.assert_array_equal(predictor.has_factor[:4], [True, False, True, False])
        # Service IDs are 10..43, completely distinct from MovieLens factor IDs.
        np.testing.assert_array_equal(predictor.factor_row[:4], [0, -1, 2, -1])

    def test_metadata_append_null_and_reordering_invariance(self):
        extra = pd.DataFrame([movie(99999)])
        extended = d.ServicePredictor(pd.concat([self.frame, extra], ignore_index=True), self.assets)
        np.testing.assert_array_equal(self.predictor.features(self.context, [1, 2, 3]),
                                      extended.features(self.context, [1, 2, 3]))
        np.testing.assert_array_equal(self.predictor.predict(self.context, [1, 2, 3])["prediction"],
                                      extended.predict(self.context, [1, 2, 3])["prediction"])
        order = np.arange(len(self.frame))[::-1]
        reordered = d.ServicePredictor(self.frame.iloc[order], self.assets)
        remap = {old: new for new, old in enumerate(order)}
        context = {**self.context, "oi": [remap[0]], "viewed": [remap[0], remap[4]]}
        np.testing.assert_array_equal(reordered.predict(context, [remap[i] for i in [1, 2, 3]])["prediction"],
                                      self.predictor.predict(self.context, [1, 2, 3])["prediction"])
        missing = self.frame.copy()
        for name in d.REQUIRED_METADATA[1:]:
            missing.at[3, name] = None
        predictor = d.ServicePredictor(missing, self.assets)
        x = predictor.features(self.context, [3])
        self.assertTrue(np.isfinite(x).all())
        self.assertEqual(x[0, self.assets.feature_names.index("crowd_rating_present")], 0)
        self.assertEqual(x[0, self.assets.feature_names.index("support_DIRECTOR_no_link")], 1)

    def test_synthetic_features_match_independent_vectorized_rh(self):
        frame = self.frame.rename(columns={"service_movie_id": "movie_id"})
        max_error = 0.
        for cap, h in [(0, 0), (1, 1), (5, 2), (10, 7), (30, 30)]:
            stars = (np.arange(h) % 10 + 1) / 2
            actual = self.predictor.features({"cap": cap, "oi": list(range(h)), "stars": stars},
                                             [30, 31, 32, 33])
            expected = batch_reference(frame, list(frame.movie_id[:h]), stars,
                                       list(frame.movie_id[30:]))
            max_error = max(max_error, float(abs(actual - expected).max()))
            np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-6)
        EVIDENCE["synthetic_rh_max_abs_error"] = max_error

    def test_invalid_context_candidates_and_metadata(self):
        invalid = [
            ({"cap": True, "oi": [], "stars": []}, [1]),
            ({"cap": 1., "oi": [], "stars": []}, [1]),
            ({"cap": 2, "oi": [], "stars": []}, [1]),
            ({"cap": 1, "oi": [0, 1], "stars": [3, 4]}, [2]),
            ({"cap": 0, "oi": [0], "stars": [3]}, [2]),
            ({"cap": 10, "oi": [0], "stars": [3.00001]}, [2]),
            ({"cap": 10, "oi": [0], "stars": [np.nan]}, [2]),
            ({"cap": 10, "oi": [0], "stars": [np.inf]}, [2]),
            ({"cap": 10, "oi": [0], "stars": [.0]}, [2]),
            ({"cap": 10, "oi": [0], "stars": [5.5]}, [2]),
            ({"cap": 10, "oi": [0], "stars": []}, [2]),
            ({"cap": 10, "oi": [0, 0], "stars": [3, 4]}, [2]),
            ({"cap": 10, "oi": [0.], "stars": [3]}, [2]),
            ({"cap": 10, "oi": [True], "stars": [3]}, [2]),
            ({"cap": 10, "oi": [-1], "stars": [3]}, [2]),
            ({"cap": 10, "oi": [34], "stars": [3]}, [2]),
            ({**self.context, "viewed": [4, 4]}, [1]),
            ({**self.context, "viewed": [4.]}, [1]),
            (self.context, [4]), (self.context, [0]), (self.context, [1, 1]),
            (self.context, [1.]), (self.context, [True]),
            (self.context, [-1]), (self.context, [34]),
        ]
        for context, candidates in invalid:
            with self.subTest(context=context, candidates=candidates), self.assertRaises(ValueError):
                self.predictor.predict(context, candidates)
        for value in [True, 1., 0, -1]:
            frame = self.frame.copy()
            frame["service_movie_id"] = frame.service_movie_id.astype(object)
            frame.at[0, "service_movie_id"] = value
            with self.subTest(service_id=value), self.assertRaises(ValueError):
                d.ServicePredictor(frame, self.assets)
        frame = self.frame.copy()
        frame.loc[1, "movielens_movie_id"] = 101
        with self.assertRaises(ValueError):
            d.ServicePredictor(frame, self.assets)
        with self.assertRaises(ValueError):
            self.predictor.predict(self.context, [1], "unregistered")
        EVIDENCE["invalid_requests_rejected"] = len(invalid)


class ReadOnlySourceTests(unittest.TestCase):
    def test_load_verified_sources_without_prediction(self):
        if not SOURCE_AUDIT:
            self.skipTest("--source-audit was not requested")
        assets = d.load_assets(REFERENCE_ROOT)
        self.assertEqual(assets.factors.shape, (45074, 32))
        self.assertEqual(len(assets.gbt.trees), 120)
        self.assertEqual(len(assets.feature_names), 230)
        self.assertIsNone(assets.calibrations[0]["ALS"])
        self.assertEqual(assets.provenance["ALS/item-factors:inventory"]["sha256"],
                         "4631e4a9ba5ecff72a0e66063bc819b3fe41a0acd364165c71af75e09f9d4f4e")
        EVIDENCE["source_entries_verified"] = len(assets.provenance)

    def test_real_metadata_feature_only_matches_sealed_score_columns(self):
        if not FEATURE_PARITY:
            self.skipTest("--feature-parity was not requested")
        base = REFERENCE_ROOT / "outputs/recommendation-evidence"
        assets = d.load_assets(REFERENCE_ROOT)
        lock = d._read(base / "final344/input-lock.json")
        prepared = d._read(base / "foundation340/prepared-seal.json")
        metadata_path = base / "rec-ev-045/metadata.parquet"
        score_path = base / "foundation340/RH/score.parquet"
        contexts_path = base / "text339/contexts.json"
        self.assertEqual(d._pin(metadata_path), lock["files"][metadata_path.relative_to(REFERENCE_ROOT).as_posix()])
        self.assertEqual(d._pin(score_path), prepared["files"]["RH/score.parquet"])
        self.assertEqual(d._pin(contexts_path),
                         d._read(base / "text339/prepared-seal.json")["files"]["contexts.json"])
        contexts = d._read(contexts_path)
        selected = []
        for cap in d.CAPS:
            selected += [c for c in contexts if c["cap"] == cap and (cap == 0 or c["h"] > 0)][:3]
        self.assertEqual(len(selected), 15)
        old_rows = sorted({int(i) for c in selected for i in list(c["oi"]) + list(c["ei"][:10])})
        metadata = pd.read_parquet(metadata_path, columns=["movie_id", *d.REQUIRED_METADATA[1:]])
        catalog = pd.read_parquet(base / "text339/catalog.parquet", columns=["movie_id"])
        np.testing.assert_array_equal(metadata.movie_id, catalog.movie_id)
        frame = metadata.iloc[old_rows].rename(columns={"movie_id": "service_movie_id"}).reset_index(drop=True)
        frame["movielens_movie_id"] = frame.service_movie_id
        frame["mapping_status"] = "MATCHED"
        predictor = d.ServicePredictor(frame, assets)
        mapping = {old: new for new, old in enumerate(old_rows)}
        columns = ["row_id", *[f"x{i:03d}" for i in range(230)]]
        self.assertNotIn("label", columns)
        source = pd.read_parquet(score_path, columns=columns).set_index("row_id")
        maximum, entries = 0., 0
        for context in selected:
            target = list(context["ei"][:10])
            supplied = {"cap": context["cap"], "oi": [mapping[i] for i in context["oi"]],
                        "stars": context["stars"]}
            actual = predictor.features(supplied, [mapping[i] for i in target])
            expected = source.loc[np.arange(context["start"], context["start"] + len(target))].to_numpy(np.float32)
            maximum = max(maximum, float(abs(actual - expected).max()) if len(target) else 0.)
            entries += actual.size
            np.testing.assert_array_equal(actual, expected)
        self.assertEqual(entries, 25070)
        EVIDENCE["real_feature_parity"] = {"contexts": len(selected), "candidate_rows": entries // 230,
                                           "feature_values": entries, "max_abs_error": maximum,
                                           "score_columns_read": columns}


class IndependentHarnessTests(unittest.TestCase):
    def test_original_independent_review_51_assertions(self):
        """Replay the independent reviewer's original fake-only inputs/formulas."""
        checks = []

        def check(condition, name):
            self.assertTrue(condition, name)
            checks.append(name)

        class FakeMetadata:
            def __init__(self, frame):
                self.names = ("fake_feature",)
                self.calls = []

            def transform(self, history, stars, candidates):
                self.calls.append((list(history), list(stars), list(candidates)))
                return np.array(candidates, dtype=np.float64).reshape(-1, 1)

        class FakeGBT:
            def __init__(self):
                self.calls = []

            def predict(self, x):
                self.calls.append(x.copy())
                return 7 + x[:, 0] / 100

        cal = {c: {"ALS": None if c == 0 else (c / 10, 2.),
                   "GBT120_s339": (-c / 10, 3.)} for c in d.CAPS}
        gbt = FakeGBT()
        assets = d.PredictorAssets(
            FakeMetadata, gbt, np.array([10, 20, 30]),
            np.array([[1., 0.], [2., 0.], [.5, 1.]]),
            np.array([10, 20, 30, 40]), np.array([1, 19, 20, 0]), cal,
            ("fake_feature",), {})
        frame = pd.DataFrame({k: [None] * 7 for k in d.REQUIRED_METADATA})
        frame["service_movie_id"] = [101, 102, 103, 104, 105, 106, 107]
        frame["mapping_status"] = ["MATCHED"] * 5 + ["AMBIGUOUS", "SERVICE_ONLY"]
        frame["movielens_movie_id"] = [10, 20, 30, 40, 999, 10, None]
        predictor = d.ServicePredictor(frame, assets)
        check(np.array_equal(predictor.factor_row, [0, 1, 2, -1, -1, -1, -1]), "service factor alignment")
        check(np.array_equal(predictor.train_count, [1, 19, 20, 0, 0, 0, 0]), "train counts and unsupported mapping")
        context = {"cap": 10, "oi": [0], "stars": [4.], "viewed": [0]}
        candidates = np.array([1, 2, 3, 4, 5, 6])
        factors = np.array([[2., 0.], [.5, 1.]])
        raw_als = factors @ np.linalg.solve(np.array([[1.1, 0.], [0., .1]]), np.array([4., 0.]))
        cal_als = 1 + 2 * raw_als
        cal_gbt = -1 + 3 * (7 + predictor.ids[candidates] / 100)
        for variant, tau in [("original", None), ("shrink20", 20), ("shrink100", 100), ("min20", None)]:
            gbt.calls.clear()
            predictor.metadata.calls.clear()
            result = predictor.predict(context, candidates, variant)
            weight = np.array([1., 1., 0., 0., 0., 0.])
            if tau:
                weight *= predictor.train_count[candidates] / (predictor.train_count[candidates] + tau)
            if variant == "min20":
                weight *= predictor.train_count[candidates] >= 20
            expected = cal_gbt * (1 - weight)
            expected[:2] += weight[:2] * cal_als
            check(np.allclose(result["prediction"], expected, rtol=0, atol=1e-12), variant + " numeric formula")
            check(result["als_rows_computed"] == int((weight > 0).sum()) and
                  result["gbt_rows_computed"] == int((weight < 1).sum()), variant + " selected head counts")
            check(np.array_equal(np.concatenate([x[:, 0] for x in gbt.calls]),
                                 predictor.ids[candidates[weight < 1]]), variant + " GBT receives only required candidates")
            check(np.array_equal(result["rating_clipped"], np.clip(expected, .5, 5)) and
                  np.any(result["prediction"] > 5), variant + " unclipped ranking")
        check(predictor.predict({"cap": 1, "oi": [0], "stars": [4.]}, [1])["prediction"][0] !=
              predictor.predict(context, [1])["prediction"][0], "requested cap not mapped length")
        gbt.calls.clear()
        check(predictor.predict(context, [1, 2])["gbt_rows_computed"] == 0 and not gbt.calls,
              "warm-only skips GBT")
        gbt.calls.clear()
        result = predictor.predict(context, [])
        check(result["candidate_count"] == 0 and not gbt.calls and len(result["prediction"]) == 0,
              "empty candidates skip scoring")
        for cap in d.CAPS:
            result = predictor.predict({"cap": cap, "oi": [], "stars": []}, [1, 2, 3])
            check(result["als_rows_computed"] == 0 and result["gbt_rows_computed"] == 3,
                  "empty history cap " + str(cap))

        def rejects(thunk, name):
            try:
                thunk()
            except (ValueError, TypeError, OverflowError):
                check(True, name)
            else:
                self.fail(name + " accepted")

        for change in [
            {"cap": True}, {"cap": 1.0}, {"cap": 2}, {"oi": [True]}, {"oi": [.0]},
            {"oi": [0, 0], "stars": [4, 4]}, {"oi": [-1]}, {"oi": [7]},
            {"stars": [3.00001]}, {"stars": [float("nan")]}, {"stars": [float("inf")]},
            {"cap": 0}, {"viewed": [False]},
        ]:
            rejects(lambda change=change: predictor.predict(dict(context, **change), [1]),
                    "invalid context " + str(change))
        for indices in [[0], [1, 1], [-1], [7], [True], [1.0]]:
            rejects(lambda indices=indices: predictor.predict(context, indices),
                    "invalid candidate " + str(indices))
        rejects(lambda: predictor.predict(dict(context, viewed=[1]), [1]), "viewed candidate exclusion")
        for change in [
            {"service_movie_id": [True, 102, 103, 104, 105, 106, 107]},
            {"service_movie_id": [101., 102., 103., 104., 105., 106., 107.]},
            {"movielens_movie_id": [10, 10, 30, 40, 999, 10, None]},
            {"movielens_movie_id": [True, 20, 30, 40, 999, 10, None]},
            {"movielens_movie_id": [10.5, 20, 30, 40, 999, 10, None]},
        ]:
            bad = frame.copy()
            for key, value in change.items():
                bad[key] = value
            rejects(lambda bad=bad: d.ServicePredictor(bad, assets), "invalid metadata " + str(list(change)))
        self.assertEqual(len(checks), 51)
        EVIDENCE["independent_synthetic_checks"] = len(checks)
        EVIDENCE["independent_synthetic_check_names"] = checks


def main():
    global REFERENCE_ROOT, SOURCE_AUDIT, FEATURE_PARITY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, default=REFERENCE_ROOT)
    parser.add_argument("--source-audit", action="store_true")
    parser.add_argument("--feature-parity", action="store_true")
    args = parser.parse_args()
    REFERENCE_ROOT = args.reference_root.resolve()
    SOURCE_AUDIT, FEATURE_PARITY = args.source_audit, args.feature_parity
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({"status": "PASS" if result.wasSuccessful() else "FAIL",
                      "tests_run": result.testsRun, "skipped": len(result.skipped),
                      "code": {p.name: d._pin(p) for p in [Path(d.__file__), Path(__file__)]},
                      **EVIDENCE}, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
