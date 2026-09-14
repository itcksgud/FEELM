"""Reviewed, bounded actual trained-head parity; no label metrics or selection.

Reference RH230 uses the independently vectorized original batch implementation.
Reference GBT traverses the pinned native Parquet nodes one row/tree at a time;
it does not call Trees.predict or reuse adapter predictions. Reference ALS uses
the source factor-ID table, manual fold-in and saved cap affine coefficients.
Future ratings in the prepared context JSON are textually redacted BEFORE JSON
decoding. Candidate sampling is deterministic by metadata ID/support, never by
future targets or ratings. Real old/current metadata and fresh-ID fixtures are
tested, including current full 237817-row feature indices.
"""
from __future__ import annotations
import argparse
import gc
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

import dv2_predictor as d
import test_dv2_predictor as reference_test

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path("C:/higher/projects/FEELM-standalone")
PREPARE_ROOT = REFERENCE / ".codex-tmp/fixed-k8-discovery-v2-20260913"
PREPARED = PREPARE_ROOT / "outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2/prepare"
OUT = ROOT / "outputs/dv2-predictor-parity-r2"
REVIEW = ROOT / "docs/recommendation/experiments/fixed-k8-discovery-v2/predictor-parity-code-review.json"
EXPECTED_SEAL = "42de5a1343e33fb6ce600d567248def48f6eb9a9c95dddfeb41d91cf408e3c76"
ATOL = 1e-8


def fingerprint():
    files = [Path(__file__), Path(d.__file__), Path(reference_test.__file__)]
    return {p.name: d._pin(p) for p in files}


def histories_only(path):
    """Delete future-label arrays before the JSON parser sees numeric values."""
    raw = path.read_bytes()
    redacted, count = re.subn(rb'"ratings"\s*:\s*\[[^\]]*\]', b'"ratings":null', raw)
    assert count == 1350, "one flat future-label array per sealed context"
    contexts = json.loads(redacted)
    assert len(contexts) == 1350 and all(c["ratings"] is None for c in contexts)
    return [{k: c[k] for k in ("uid", "cap", "history", "stars", "viewed")}
            for c in contexts], count


class NativeScalarGBT:
    """Independent scalar evaluation of frozen native Spark trees."""
    def __init__(self, native):
        nodes = pd.read_parquet(native / "data")
        weights = pd.read_parquet(native / "treesMetadata")
        self.models = []
        for row in weights.sort_values("_1").to_dict("records"):
            tree_id, weight = int(row["_1"]), float(row["_3"])
            tree = {int(n["id"]): n for n in nodes.loc[nodes.treeID.eq(tree_id), "nodeData"]}
            self.models.append((weight, tree))
        assert len(self.models) == 120

    def predict(self, x):
        output = np.zeros(len(x), dtype=np.float64)
        for i, features in enumerate(np.asarray(x, np.float32)):
            for weight, tree in self.models:
                node = tree[0]
                while node["leftChild"] != -1:
                    split = node["split"]
                    value = float(features[int(split["featureIndex"])])
                    left = value <= float(split["leftCategoriesOrThreshold"][0])
                    node = tree[int(node["leftChild"] if left else node["rightChild"])]
                output[i] += weight * float(node["prediction"])
        return output


def source_support(frame, reference_catalog, factor_table):
    train = dict(zip(reference_catalog.movie_id, reference_catalog.train_count))
    factor = {int(r.id): np.asarray(r.features, float) for r in factor_table.itertuples()}
    counts = np.zeros(len(frame), np.int64)
    vectors = np.full((len(frame), 32), np.nan)
    for i, row in enumerate(frame.itertuples()):
        if row.mapping_status != "MATCHED" or pd.isna(row.movielens_movie_id):
            continue
        movie = int(row.movielens_movie_id)
        counts[i] = train.get(movie, 0)
        if movie in factor:
            vectors[i] = factor[movie]
    return counts, vectors


def choose_candidates(frame, count, viewed):
    seen = set(viewed)
    priority = sorted(range(len(frame)), key=lambda i: hashlib.sha256(
        f"dv2-prediction-parity:{int(frame.service_movie_id.iloc[i])}".encode()).digest())
    chosen = []
    for low, high in [(0, 0), (1, 5), (6, 20), (21, 100), (101, np.inf)]:
        chosen += [i for i in priority if i not in seen and low <= count[i] <= high][:4]
    # More missing-evidence cases, independent of targets or model output.
    missing = frame.genre_ids.map(lambda x: x is None or len(x) == 0).to_numpy()
    chosen += [i for i in priority if i not in seen and missing[i]][:4]
    return np.asarray(list(dict.fromkeys(chosen)), int)


def independent_heads(frame, context, candidates, vectors, count, native, calibrations):
    history = context["history"]
    rows = list(dict.fromkeys(list(history) + list(candidates)))
    local = frame.iloc[rows][list(d.REQUIRED_METADATA)].rename(columns={"service_movie_id": "movie_id"})
    x = reference_test.batch_reference(local, frame.service_movie_id.iloc[history].tolist(),
                                       context["stars"], frame.service_movie_id.iloc[candidates].tolist())
    raw_gbt = native.predict(x)
    fits = calibrations[context["cap"]]
    cal_gbt = fits["GBT120_s339"][0] + fits["GBT120_s339"][1] * raw_gbt
    good_history = np.isfinite(vectors[history]).all(1)
    available = np.isfinite(vectors[candidates]).all(1) & bool(good_history.any())
    raw_als = np.full(len(candidates), np.nan)
    cal_als = raw_als.copy()
    if good_history.any():
        y = vectors[np.asarray(history)[good_history]]
        ratings = np.asarray(context["stars"])[good_history]
        user = np.linalg.solve(y.T @ y + .1 * len(y) * np.eye(32), y.T @ ratings)
        # Scalar candidate dots also check vectorized evaluation rounding.
        raw_als[available] = [float(np.dot(v, user)) for v in vectors[candidates[available]]]
        a, b = fits["ALS"]
        cal_als[available] = a + b * raw_als[available]
    return x, raw_als, raw_gbt, cal_als, cal_gbt, available


def run():
    start_review_pin = d._pin(REVIEW)
    review = d._read(REVIEW)
    assert review["status"] == "PASS" and review["fingerprint"] == fingerprint()
    assert review["prepare_seal_sha256"] == EXPECTED_SEAL
    assert not OUT.exists(), "preserve previous parity outcomes"
    start = time.perf_counter()
    initial_fingerprint = fingerprint()
    seal_path = PREPARED.parent / "prepare-seal.json"
    assert d._pin(seal_path)["sha256"] == EXPECTED_SEAL
    seal = d._read(seal_path)
    for name in ["catalog.parquet", "contexts.json"]:
        assert d._pin(PREPARED / name) == seal["files"][str(Path("prepare") / name)]
    assets = d.load_assets(REFERENCE)
    start_provenance = dict(assets.provenance)
    base = REFERENCE / "outputs/recommendation-evidence"
    source_calibration = d._read(base / "hybrid345/calibration.json")
    reference_calibrations = {}
    for cap in d.CAPS:
        reference_calibrations[cap] = {}
        for head in ["ALS", "GBT120_s339"]:
            entries = [row for row in source_calibration["fits"] if row["cap"] == cap and row["model"] == head]
            assert len(entries) == 1
            row = entries[0]
            fit = None if row["a"] is None else (float(row["a"]), float(row["b"]))
            assert fit is not None or cap == 0 and head == "ALS"
            reference_calibrations[cap][head] = fit
    catalog = pd.read_parquet(base / "text339/catalog.parquet", columns=["movie_id", "train_count"])
    factor_table = pd.read_parquet(base / "combination340/ALS/item-factors")
    native = NativeScalarGBT(base / "final344/GBT120_s339/model/native")
    original_metadata_path = base / "rec-ev-045/metadata.parquet"
    lock = d._read(base / "final344/input-lock.json")
    start_metadata_pin = d._pin(original_metadata_path)
    assert start_metadata_pin == lock["files"][original_metadata_path.relative_to(REFERENCE).as_posix()]
    original_context_path = base / "text339/contexts.json"
    start_context_pin = d._pin(original_context_path)
    assert start_context_pin == d._read(base / "text339/prepared-seal.json")["files"]["contexts.json"]
    old_contexts = d._read(original_context_path)
    assert all("ratings" not in c and "label" not in c for c in old_contexts)
    service_contexts, redactions = histories_only(PREPARED / "contexts.json")
    service_context_map = {(c["uid"], c["cap"]): c for c in service_contexts}
    old = pd.read_parquet(original_metadata_path, columns=["movie_id", *d.REQUIRED_METADATA[1:]])
    np.testing.assert_array_equal(old.movie_id, catalog.movie_id)
    old = old.rename(columns={"movie_id": "service_movie_id"})
    old["movielens_movie_id"] = old.service_movie_id
    old["mapping_status"] = "MATCHED"
    contexts = []
    for cap in d.CAPS:
        options = [c for c in old_contexts if c["cap"] == cap and (cap == 0 or c["als_supported_inputs"] > 0)]
        source = min(options, key=lambda c: hashlib.sha256(f"dv2-parity-user:{c['uid']}".encode()).digest())
        contexts.append({"uid": source["uid"], "cap": cap, "history": source["oi"],
                         "stars": source["stars"], "viewed": source["viewed"]})
    records, summaries, old_outputs = [], [], {}
    reference_test.REFERENCE_ROOT = REFERENCE

    def check_scene(name, frame, scene_contexts, candidate_override=None):
        count, vectors = source_support(frame, catalog, factor_table)
        predictor = d.ServicePredictor(frame, assets)
        np.testing.assert_array_equal(predictor.train_count, count)
        np.testing.assert_array_equal(predictor.has_factor, np.isfinite(vectors).all(1))
        max_feature = max_score = 0.
        warm_total = cold_total = calls = head_rows = 0
        for context in scene_contexts:
            key = context["uid"], context["cap"]
            candidates = (candidate_override[key] if candidate_override is not None else
                          choose_candidates(frame, count, context["viewed"]))
            assert len(candidates) > 0 and not set(candidates) & set(context["viewed"])
            x, raw_als, raw_gbt, cal_als, cal_gbt, available = independent_heads(
                frame, context, candidates, vectors, count, native, reference_calibrations)
            supplied = {"cap": context["cap"], "oi": context["history"],
                        "stars": context["stars"], "viewed": context["viewed"]}
            actual_x = predictor.features(supplied, candidates)
            max_feature = max(max_feature, float(abs(x - actual_x).max()))
            np.testing.assert_allclose(actual_x, x, rtol=0, atol=2e-6)
            warm_total += int(available.sum())
            cold_total += int((~available).sum())
            for variant in d.VARIANTS:
                weight = available.astype(float)
                if variant.startswith("shrink"):
                    weight *= count[candidates] / (count[candidates] + int(variant[6:]))
                elif variant == "min20":
                    weight *= count[candidates] >= 20
                expected = cal_gbt.copy()
                expected[available] = weight[available] * cal_als[available] + (1 - weight[available]) * cal_gbt[available]
                actual = predictor.predict(supplied, candidates, variant)
                calls += 1
                head_rows += actual["als_rows_computed"] + actual["gbt_rows_computed"]
                use_als, use_gbt = weight > 0, weight < 1
                for field, wanted, used in [("raw_als", raw_als, use_als), ("calibrated_als", cal_als, use_als),
                                             ("raw_gbt", raw_gbt, use_gbt), ("calibrated_gbt", cal_gbt, use_gbt)]:
                    np.testing.assert_allclose(actual[field][used], wanted[used], rtol=0, atol=ATOL)
                    assert np.isnan(actual[field][~used]).all()
                np.testing.assert_allclose(actual["prediction"], expected, rtol=0, atol=ATOL)
                np.testing.assert_array_equal(actual["als_weight"], weight)
                np.testing.assert_array_equal(actual["rating_clipped"], np.clip(actual["prediction"], .5, 5))
                expected_branch = np.where(weight == 1, "ALS", np.where(weight > 0, "ALS_GBT_SHRINK", "GBT"))
                np.testing.assert_array_equal(actual["branch"], expected_branch)
                assert actual["als_rows_computed"] == int(use_als.sum()) and actual["gbt_rows_computed"] == int(use_gbt.sum())
                reversed_result = predictor.predict(supplied, candidates[::-1], variant)
                pieces = [predictor.predict(supplied, candidates[i:i + 7], variant)
                          for i in range(0, len(candidates), 7)]
                calls += 1 + len(pieces)
                np.testing.assert_allclose(reversed_result["prediction"][::-1], actual["prediction"], rtol=0, atol=ATOL)
                np.testing.assert_allclose(np.concatenate([part["prediction"] for part in pieces]), actual["prediction"], rtol=0, atol=ATOL)
                if name == "old_metadata":
                    old_outputs[key, variant] = actual["prediction"].copy()
                elif name == "old_metadata_fresh_service_ids":
                    np.testing.assert_array_equal(actual["prediction"], old_outputs[key, variant])
                error = float(abs(actual["prediction"] - expected).max())
                max_score = max(max_score, error)
                for j, index in enumerate(candidates):
                    records.append({"scenario": name, "uid": context["uid"], "cap": context["cap"], "variant": variant,
                                    "service_movie_id": int(frame.service_movie_id.iloc[index]), "train_count": int(count[index]),
                                    "als_available": bool(available[j]), "als_weight": float(weight[j]),
                                    "actual_raw_als": actual["raw_als"][j], "reference_raw_als": raw_als[j],
                                    "actual_raw_gbt": actual["raw_gbt"][j], "reference_raw_gbt": raw_gbt[j],
                                    "actual_prediction": actual["prediction"][j], "reference_prediction": expected[j]})
            assert time.perf_counter() - start < 600 and len(records) < 10000
        summaries.append({"scenario": name, "index_rows": len(frame), "contexts": len(scene_contexts),
                          "warm_candidate_rows": warm_total, "cold_candidate_rows": cold_total,
                          "main_candidate_head_rows": head_rows, "actual_predict_calls": calls,
                          "max_feature_abs_error": max_feature, "max_prediction_abs_error": max_score})
        del predictor
        gc.collect()
        return count

    old_count, _ = source_support(old, catalog, factor_table)
    old_candidates = {(c["uid"], c["cap"]): choose_candidates(old, old_count, c["viewed"]) for c in contexts}
    check_scene("old_metadata", old, contexts, old_candidates)
    renamed = old.copy()
    renamed["service_movie_id"] = renamed.service_movie_id + 10000000
    check_scene("old_metadata_fresh_service_ids", renamed, contexts, old_candidates)
    del old, renamed
    gc.collect()
    current = pd.read_parquet(PREPARED / "catalog.parquet")
    current_contexts = [service_context_map[c["uid"], c["cap"]] for c in contexts]
    check_scene("current_full_service_axis", current, current_contexts)
    warm_context = next(c for c in current_contexts if c["cap"] == 10)
    source_rows = sorted(set(warm_context["history"] + [0, 1, 2, 3]))
    local = current.iloc[source_rows].copy().reset_index(drop=True)
    mapping = {old: new for new, old in enumerate(source_rows)}
    clones = current.iloc[[0, 1, 2, 3]].copy()
    clones["service_movie_id"] = np.arange(int(current.service_movie_id.max()) + 1,
                                          int(current.service_movie_id.max()) + 5)
    clones["mapping_status"] = "SERVICE_ONLY"
    clones["movielens_movie_id"] = np.nan
    fixture = pd.concat([local, clones], ignore_index=True)
    clone_context = {**warm_context, "history": [mapping[i] for i in warm_context["history"]],
                     "viewed": [mapping[i] for i in warm_context["viewed"] if i in mapping]}
    clone_candidates = {(clone_context["uid"], clone_context["cap"]): np.arange(len(local), len(fixture))}
    check_scene("fresh_unmatched_metadata_clones", fixture, [clone_context], clone_candidates)
    assert all(s["warm_candidate_rows"] > 0 and s["cold_candidate_rows"] > 0 for s in summaries[:3])
    assert summaries[3]["warm_candidate_rows"] == 0 and summaries[3]["cold_candidate_rows"] == 4
    assert fingerprint() == initial_fingerprint and d._pin(seal_path)["sha256"] == EXPECTED_SEAL
    assert d._pin(REVIEW) == start_review_pin
    assert d._pin(original_metadata_path) == start_metadata_pin and d._pin(original_context_path) == start_context_pin
    assert d.load_assets(REFERENCE).provenance == start_provenance
    for name in ["catalog.parquet", "contexts.json"]:
        assert d._pin(PREPARED / name) == seal["files"][str(Path("prepare") / name)]
    OUT.mkdir(parents=True)
    pd.DataFrame(records).to_parquet(OUT / "parity-rows.parquet", index=False)
    result = {"status": "PASS", "scope": "Actual trained-head/route parity only; no quality metrics or model selection",
              "fingerprint": initial_fingerprint, "prepare_seal_sha256": EXPECTED_SEAL,
              "review": start_review_pin, "scenarios": summaries, "parity_rows": len(records), "absolute_tolerance": ATOL,
              "future_ratings_arrays_redacted_before_json_parse": redactions,
              "future_label_values_parsed": False, "future_label_metrics_or_selection": False,
              "candidate_sampling": "Fixed SHA256 metadata-ID order, four per train-support bin plus four missing-genre cases; independent of future targets/ratings",
              "native_reference": "Independent scalar native-Parquet GBT traversal plus independent batch RH230 and source-ID ALS fold-in",
              "original_metadata": start_metadata_pin, "original_past_contexts": start_context_pin,
              "reference_calibrations": reference_calibrations, "model_sources": start_provenance,
              "seconds": time.perf_counter() - start}
    (OUT / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    sealed = {"status": "PASS", "files": {p.name: d._pin(p) for p in [OUT / "report.json", OUT / "parity-rows.parquet"]}}
    (OUT / "parity-seal.json").write_text(json.dumps(sealed, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "parity_rows": len(records), "scenarios": summaries,
                      "report": d._pin(OUT / "report.json"), "seal": d._pin(OUT / "parity-seal.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"fingerprint": fingerprint(), "prepare_seal_sha256": EXPECTED_SEAL}))
    else:
        with threadpool_limits(limits=2):
            run()
