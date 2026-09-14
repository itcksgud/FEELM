"""Read-only independent raw metadata/quality/history and service-axis RH audit.

No trained predictor is called and no future rating/label column is loaded.
Streams both original TMDb archives, compares every service row, then compares
sample RH230 features on the complete service index against an independently
vectorized reference on just the same history/candidate metadata rows.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import replace
import json
import math
from pathlib import Path
import tarfile
import time

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

import dv2_predictor as d
import test_dv2_predictor as reference_test


def equal(left, right):
    if isinstance(left, np.ndarray):
        left = left.tolist()
    if isinstance(right, np.ndarray):
        right = right.tolist()
    if isinstance(left, list) or isinstance(right, list):
        return left == right
    if left is None or (isinstance(left, float) and math.isnan(left)):
        return right is None or (isinstance(right, float) and math.isnan(right))
    return left == right


def old_ids(records, limit=None):
    """The original integer identity semantics, with ordered unique cast cap."""
    result = []
    for item in records or []:
        try:
            value = int(item.get("id"))
        except (ValueError, TypeError):
            continue
        if value > 0 and value not in result:
            result.append(value)
        if limit is not None and len(result) == limit:
            break
    return result


def number(body, key, zero=False):
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) and (value > 0 or zero and value == 0) else None


def state(body, key, *, maximum=None, integer=False):
    if key not in body:
        return "MISSING"
    value = body[key]
    if value is None:
        return "NULL"
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return "NON_NUMERIC"
    if not math.isfinite(value):
        return "NON_FINITE"
    if value < 0 or maximum is not None and value > maximum:
        return "OUT_OF_RANGE"
    if integer and int(value) != value:
        return "NON_INTEGER"
    return "ZERO" if value == 0 else "POSITIVE"


def audit(prepare_root, reference_root, snapshot):
    started = time.perf_counter()
    root, original = Path(prepare_root).resolve(), Path(reference_root).resolve()
    output = root / "outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r1"
    prepared = output / "prepare"
    seal_pin = d._pin(output / "prepare-seal.json")
    seal = d._read(output / "prepare-seal.json")
    for name, expected in seal["files"].items():
        assert d._pin(output / name) == expected, ("prepare seal mismatch", name)
    frame = pd.read_parquet(prepared / "catalog.parquet")
    assert len(frame) == 237817 and frame.service_movie_id.is_unique and frame.tmdb_id.is_unique
    assert (np.diff(frame.service_movie_id) > 0).all()
    old_output = original / ".codex-tmp/fixed-k8-discovery-20260912/outputs/fixed-k8-discovery"
    old_prepare_seal = old_output / "prepare-seal.json"
    assert d._pin(old_prepare_seal)["sha256"] == "963a5bd3a4598c3ccf8fa0dd059e89761951d36755c0c0efb5b1f1105b9d6844"
    old_catalog = old_output / "prepare/catalog.parquet"
    assert d._pin(old_catalog) == d._read(old_prepare_seal)["files"]["prepare/catalog.parquet"]
    axis_columns = ["service_movie_id", "tmdb_id", "mapping_status", "movielens_movie_id"]
    source_axis = pd.read_parquet(old_catalog, columns=axis_columns)
    pd.testing.assert_frame_equal(frame[axis_columns], source_axis, check_exact=True)
    original_assignments = old_output / "final/assignments.parquet"
    assert d._pin(original_assignments)["sha256"] == "095c157daa77ffdd6d09fd4f33341b51bed81890f57095492112b5ef75a54708"
    old_top = pd.read_parquet(original_assignments, columns=["service_movie_id", "taste_id"])
    pd.testing.assert_frame_equal(frame[["service_movie_id", "taste_id"]], old_top, check_exact=True, check_dtype=False)
    np.testing.assert_array_equal(frame.taste_id, np.load(prepared / "top.npy"))
    arrays = {name: frame[name].tolist() for name in frame}
    lookup = {int(mid): i for i, mid in enumerate(frame.tmdb_id)}
    mismatches, samples = Counter(), []
    seen, keyword_seen = set(), set()
    raw_states, metadata_types = Counter(), Counter()

    def compare(i, name, expected, *, identity_set=False):
        actual = arrays[name][i]
        if identity_set:
            ok = set(actual) == set(expected)
        else:
            ok = equal(actual, expected)
        if not ok:
            mismatches[name] += 1
            if len(samples) < 20:
                samples.append({"tmdb_id": int(frame.tmdb_id.iloc[i]), "column": name,
                                "actual": str(actual), "expected": str(expected)})

    source_paths = [Path(snapshot) / name for name in (
        "02_tmdb-movie-raw_20231013-20260907.tar.gz", "03_tmdb-keywords-raw_20231013-20260907.tar.gz")]
    expected_hashes = ["cb4a9f119bdc349841f9245723f24f911f823f65e25bf4ce08ae03fce335e10e",
                       "e6681572435284c721425ea1d8b31623cbb0f75804aafcb2fa370eb4bd4b7326"]
    source_pins = {path.name: d._pin(path) for path in source_paths}
    assert [source_pins[p.name]["sha256"] for p in source_paths] == expected_hashes
    source_pins.update({"v1_catalog": d._pin(old_catalog), "v1_prepare_seal": d._pin(old_prepare_seal),
                        "v1_top_assignments": d._pin(original_assignments)})
    with tarfile.open(source_paths[0], "r|gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            raw = json.load(archive.extractfile(member))
            body, credits = raw["details"], raw.get("credits") or {}
            mid = int(Path(member.name).stem)
            assert body["id"] == mid and mid in lookup and mid not in seen
            assert not credits or credits.get("id", mid) == mid
            seen.add(mid)
            i = lookup[mid]
            directors = old_ids([x for x in credits.get("crew") or [] if x.get("job") == "Director"])
            cast = sorted(credits.get("cast") or [],
                          key=lambda x: (int(x.get("order", 1000000)), int(x.get("id", 1000000))))
            collection = body.get("belongs_to_collection")
            date = body.get("release_date") or ""
            try:
                year = int(date[:4]) if len(date) >= 4 else None
            except ValueError:
                year = None
            runtime = body.get("runtime")
            metadata_types["runtime:" + type(runtime).__name__] += 1
            try:
                runtime = int(runtime) if runtime is not None else None
                runtime = runtime if runtime is not None and runtime > 0 else None
            except (ValueError, TypeError):
                runtime = None
            expected = {
                "genre_ids": sorted(old_ids(body.get("genres"))),
                "director_ids": directors, "top5_cast_ids": old_ids(cast, 5),
                "production_company_ids": old_ids(body.get("production_companies")),
                "collection_ids": old_ids([collection]) if isinstance(collection, dict) else [],
                "production_country_codes": sorted({x["iso_3166_1"] for x in body.get("production_countries") or [] if x.get("iso_3166_1")}),
                "release_year": year, "runtime_minutes": runtime,
                "tmdb_vote_average": number(body, "vote_average"),
                "tmdb_vote_count": number(body, "vote_count", True),
                "tmdb_popularity": number(body, "popularity", True),
                "original_language": (body.get("original_language") or "").strip(),
                "release_date": date, "status": body.get("status") or "",
                "overview": str(body.get("overview") or "").strip(), "raw_credits_present": "credits" in raw,
                "raw_source_member": member.name, "raw_tar_mtime": member.mtime,
            }
            for name, value in expected.items():
                compare(i, name, value, identity_set=name in ["genre_ids", "director_ids", "production_company_ids", "collection_ids"])
            rs, vs = state(body, "vote_average", maximum=10), state(body, "vote_count", integer=True)
            compare(i, "raw_vote_average_state", rs)
            compare(i, "raw_vote_count_state", vs)
            assert json.loads(arrays["raw_vote_average_json"][i]) == body.get("vote_average")
            assert json.loads(arrays["raw_vote_count_json"][i]) == body.get("vote_count")
            for name in ["vote_average", "vote_count"]:
                value = body.get(name)
                raw_number = float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None
                compare(i, "raw_" + name + "_number", raw_number)
            r, v = body.get("vote_average"), body.get("vote_count")
            if rs not in ("ZERO", "POSITIVE"):
                quality = "R_" + rs
            elif vs not in ("ZERO", "POSITIVE"):
                quality = "V_" + vs
            elif v == 0:
                quality = "CONTRADICTORY_ZERO_COUNT" if r > 0 else "ZERO_VOTES"
            elif r == 0:
                quality = "AMBIGUOUS_ZERO_AVERAGE"
            else:
                quality = "VALID"
            compare(i, "quality_state", quality)
            raw_states[quality] += 1
            for flag in ["adult", "video"]:
                value = body.get(flag)
                flagstate = "MISSING" if flag not in body else "NULL" if value is None else "TRUE" if value is True else "FALSE" if value is False else "INVALID"
                compare(i, "raw_" + flag + "_state", flagstate)
            stamps = {k: value for k, value in raw.items() if any(t in k.lower() for t in ["fetch", "time", "date"])}
            assert json.loads(arrays["raw_timestamp_fields"][i]) == stamps
            if len(seen) % 50000 == 0:
                print("RAW_AUDIT", len(seen), "mismatches", dict(mismatches), flush=True)
    assert seen == set(lookup)
    with tarfile.open(source_paths[1], "r|gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            raw = json.load(archive.extractfile(member))
            mid = int(Path(member.name).stem)
            assert raw["id"] == mid and mid not in keyword_seen
            keyword_seen.add(mid)
            if mid in lookup:
                compare(lookup[mid], "keyword_ids", old_ids(raw.get("keywords")), identity_set=True)
                compare(lookup[mid], "keyword_file_present", True)
    for mid in set(lookup) - keyword_seen:
        compare(lookup[mid], "keyword_ids", [], identity_set=True)
        compare(lookup[mid], "keyword_file_present", False)
    print("RAW_COMPLETE", len(seen), "mismatches", dict(mismatches), flush=True)

    config_path = root / "docs/recommendation/experiments/fixed-k8-discovery-v2/config.json"
    review_path = config_path.with_name("prepare-execution-review.json")
    config_pin = d._pin(config_path)
    assert d._read(review_path)["fingerprint"]["files"][str(config_path)] == config_pin
    source_pins.update({"prepare_config": config_pin, "prepare_execution_review": d._pin(review_path)})
    config = d._read(config_path)
    content = np.load(prepared / "content.npy", mmap_mode="r")
    dates = pd.to_datetime(frame.release_date, format="%Y-%m-%d", errors="coerce")
    base = ((dates.notna() & (dates <= pd.Timestamp(config["candidate_date"])) &
             frame.status.eq("Released") & frame.raw_adult_state.eq("FALSE") &
             frame.raw_video_state.eq("FALSE")).to_numpy() & ((content * content).sum(1) > 1e-12))
    np.testing.assert_array_equal(base, np.load(prepared / "base-eligible.npy"))
    valid = frame.quality_state.eq("VALID").to_numpy()
    eligible = base & valid
    r, v = frame.raw_vote_average_number.to_numpy(float), frame.raw_vote_count_number.to_numpy(float)
    center, mass = float(r[eligible].mean()), config["quality_m"]
    quality = np.full(len(frame), np.nan)
    quality[valid] = (r[valid] * v[valid] + mass * center) / (v[valid] + mass)
    np.testing.assert_array_equal(quality, np.load(prepared / "quality.npy"))
    ix = np.flatnonzero(eligible)
    expected_order = ix[np.lexsort((frame.service_movie_id.to_numpy()[ix], -v[ix], -quality[ix]))]
    np.testing.assert_array_equal(expected_order, np.load(prepared / "quality-order.npy"))
    report = d._read(prepared / "report.json")
    assert report["quality_states"] == dict(raw_states)
    assert report["base_eligible"] == int(base.sum()) and report["quality_eligible"] == int(eligible.sum())
    assert report["main_quality"]["C"] == center and report["main_quality"]["m"] == mass

    contexts = d._read(prepared / "contexts.json")
    old_contexts_path = original / ".codex-tmp/fixed-k8-discovery-20260912/outputs/fixed-k8-discovery/recommend/contexts.json"
    old_seal_path = old_contexts_path.parent.parent / "recommend-seal.json"
    assert d._pin(old_seal_path)["sha256"] == "2f83b17ef6a1959814354105f1d7a10ab85c9d6499e37b17d77bb13e62681ccd"
    assert d._pin(old_contexts_path) == d._read(old_seal_path)["files"]["recommend/contexts.json"]
    text_seal_path = original / "outputs/recommendation-evidence/text339/prepared-seal.json"
    assert d._pin(text_seal_path)["sha256"] == d.SOURCE_SHA256["text339/prepared-seal.json"]
    text_contexts_path = text_seal_path.parent / "contexts.json"
    assert d._pin(text_contexts_path) == d._read(text_seal_path)["files"]["contexts.json"]
    source_pins.update({"original_viewed_contexts": d._pin(old_contexts_path),
                        "original_viewed_contexts_seal": d._pin(old_seal_path),
                        "text339_contexts": d._pin(text_contexts_path),
                        "text339_contexts_seal": d._pin(text_seal_path)})
    old_context_rows, text_context_rows = d._read(old_contexts_path), d._read(text_contexts_path)
    old_contexts = {(c["uid"], c["cap"]): c for c in old_context_rows}
    text_contexts = {(c["uid"], c["cap"]): c for c in text_context_rows}
    assert len(contexts) == len(old_contexts) == len(text_contexts) == 1350
    keys = {(c["uid"], c["cap"]) for c in contexts}
    assert len(keys) == len(contexts) and keys == set(old_contexts) == set(text_contexts)
    assert len(old_context_rows) == len(old_contexts) and len(text_context_rows) == len(text_contexts)
    for context in contexts:
        prior = old_contexts[context["uid"], context["cap"]]
        assert all(context[k] == value for k, value in prior.items())
        source = text_contexts[context["uid"], context["cap"]]
        assert context["original_stars"] == source["stars"]
        assert context["original_input_timestamps"] == source["input_timestamps"]
        assert context["original_input_count"] == len(source["oi"]) <= context["cap"]
        assert max(source["input_timestamps"], default=0) < config["history_origin"]
        assert set(context["history"]) <= set(context["viewed"])
        assert not set(context["history"]) & set(context["target"])

    reference_test.REFERENCE_ROOT = original
    assets = d.load_assets(original)
    class NoPrediction:
        def predict(self, features):
            raise AssertionError("trained prediction is forbidden in this audit")
    assets = replace(assets, gbt=NoPrediction())
    predictor = d.ServicePredictor(frame, assets)
    samples_context = []
    for cap in d.CAPS:
        samples_context += [c for c in contexts if c["cap"] == cap and
                            (cap == 0 or len(c["history"]) > 0)][:3]
    assert len(samples_context) == 15
    max_error, feature_rows = 0., 0
    for context in samples_context:
        candidates = list(context["target"][:10])
        if not candidates:
            continue
        history = context["history"]
        indices = list(dict.fromkeys(history + candidates))
        local = frame.iloc[indices][list(d.REQUIRED_METADATA)].rename(columns={"service_movie_id": "movie_id"}).copy()
        wanted_history = frame.service_movie_id.iloc[history].tolist()
        wanted_candidates = frame.service_movie_id.iloc[candidates].tolist()
        actual = predictor.features({"cap": context["cap"], "oi": history, "stars": context["stars"],
                                     "viewed": context["viewed"]}, candidates)
        expected = reference_test.batch_reference(local, wanted_history, context["stars"], wanted_candidates)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-6)
        max_error = max(max_error, float(abs(actual - expected).max()))
        feature_rows += len(candidates)
    assert feature_rows > 0
    assert d._pin(output / "prepare-seal.json") == seal_pin
    actual_inventory = {str(path.relative_to(output)): d._pin(path)
                        for path in prepared.rglob("*") if path.is_file()}
    assert actual_inventory == seal["files"]
    assert d._pin(config_path) == config_pin
    result = {"status": "PASS" if not mismatches else "FAIL", "prepare_seal": seal_pin,
              "catalog": d._pin(prepared / "catalog.parquet"), "source_pins": source_pins,
              "raw_rows_compared": len(seen), "keyword_source_rows": len(keyword_seen),
              "metadata_mismatches": dict(mismatches), "mismatch_examples": samples,
              "raw_quality_states": dict(raw_states), "metadata_types": dict(metadata_types),
              "base_eligible": int(base.sum()), "quality_eligible": int(eligible.sum()), "C": center, "m": mass,
              "contexts_equal_to_sealed_history_source": len(contexts),
              "service_tmdb_movielens_axis_equal": len(frame), "top_assignments_equal": len(frame),
              "service_factor_items": int(predictor.has_factor.sum()),
              "service_train_rating_support_sum": int(predictor.train_count.sum()),
              "service_axis_rh_parity": {"index_rows": len(frame), "contexts": len(samples_context),
                                          "candidate_rows": feature_rows, "max_abs_error": max_error},
              "trained_model_predictions": 0, "future_label_columns_read": 0,
              "seconds": time.perf_counter() - started,
              "code": {p.name: d._pin(p) for p in [Path(__file__), Path(d.__file__), Path(reference_test.__file__)]}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-root", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, default=Path("C:/higher/projects/FEELM-standalone"))
    parser.add_argument("--snapshot", type=Path,
                        default=Path("C:/higher/projects/FEELM-standalone/.codex-tmp/rec038-korean-movie-guide-20260911/outputs/service-catalog-snapshot-20260909"))
    args = parser.parse_args()
    with threadpool_limits(limits=4):
        result = audit(args.prepare_root, args.reference_root, args.snapshot)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
