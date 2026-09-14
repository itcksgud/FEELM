"""Candidate-only service-axis adapter for the frozen hybrid345 predictor.

This local research module does not fit models, select policies, read labels, or
read old per-user predictions. ``load_assets(reference_root)`` verifies the
original sealed model sources. ``ServicePredictor(metadata, assets)`` builds
identity-overlap metadata indices once; it does not score catalog rows.

The metadata frame has one row per service movie, in the caller's canonical
order. REQUIRED_METADATA lists the normalized RH230 fields; original field
presence/null/invalid flags belong in separate columns and are not features.
Lists may be null (meaning unavailable), numeric fields may be NaN, and language
may be null. Do not replace missing metadata with negative preference evidence.
The caller must preserve the original RH normalization: runtime <= 0 unavailable;
vote_average <= 0 unavailable; vote_count/popularity zero valid; director job is
exactly Director; cast is ordered by (order, id), keeping first 5 unique IDs.
Production countries must not be replaced by origin countries.

Context: {cap: 0|1|5|10|30, oi: service row indices, stars: actual half-stars,
viewed: all known viewed service row indices}. Apply the cap to original latest-
first rated records BEFORE mapping them to this catalog. ``oi`` is the resulting
actual history, never padded or refilled. Calibration uses requested cap, not
mapped history length. The caller owns past-time and candidate-eligibility gates.
The adapter enforces that candidates are unique and exclude all viewed/history.

``predict(context, indices, variant='original')`` computes only requested heads
for these candidate indices. Original = calibrated ALS when item factors and
at least one history factor exist, else calibrated GBT120_s339. Qwen weight is 0.
shrink20/100 blend calibrated ALS with calibrated GBT using n/(n+tau), where n
is the item's frozen MovieLens TRAIN count. min20 uses GBT below train count 20.
These are distinct research alternatives, not changes to the original model.
Ranking uses UNCLIPPED calibrated values; rating_clipped is for display/error
metrics, matching hybrid345. TMDb quality scores never enter these blends.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import operator
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
import pandas as pd

CAPS = (0, 1, 5, 10, 30)
VARIANTS = ("original", "shrink20", "shrink100", "min20")
REQUIRED_METADATA = (
    "service_movie_id", "genre_ids", "keyword_ids", "production_country_codes",
    "original_language", "director_ids", "top5_cast_ids",
    "production_company_ids", "collection_ids", "release_year",
    "runtime_minutes", "tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity",
)
REFERENCE_CODE_SHA256 = {
    "final344_adapter.py": "15b6a53c5be9e6207cb215857058570304b61c17c5aa88198341efd55f688894",
    "cold_item_features.py": "78ed5bbea151dc12d7a8bb144fa389333c779550fcfdff66d28d90c6900a4cec",
    "text339_relations.py": "fdc970a6506cfd27dcc2df15cd2785ce5f221f76bd74eaeb24aa6d73c5ccd4d6",
    "rec047_features.py": "a6f3fdc26cc88cceea2f6b1fe1244285bd9e29f7175874f7ab2eb8fae5904268",
    "rec046_common.py": "42dd14e83ecbee0c02c5e4233abb4b6a8d807ad2213c24a22cfb82350bc47848",
    "combination340_models.py": "a519586b25d81574a96e1847dc6a4d8d08d3ded276deff64c5d82ff0bcdecd25",
}
SOURCE_SHA256 = {
    "text339/prepared-seal.json": "d27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8",
    "combination340/fit-seal.json": "702fb7fd8b82dabd607811064ed02fdd6e46cd319e718072a01a00328c1e7282",
    "final344/GBT120_s339-seal.json": "ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7",
    "final344/input-lock.json": "487f2fdc8a216b39907af3bf3bbae1eb4790384a51572b36861248adf7dd3fc6",
    "foundation340/RH/feature-info.json": "8b871f51b027b5ae3fb8c52ac4fa2b67bee7b3e819d06c3f4389a425e9549094",
    "foundation340/prepared-seal.json": "9f0550e38566512fc2dad084d3f9381a7197ea3bab05fd0428eef76878acb62e",
    "hybrid345/calibration.json": "6b2baf1dd27997cffe7f69fdc1e69e9fe80d26a29f467ee53958c2dc6f46e562",
    "hybrid345/selection.json": "cc22115e3b8f5e216bcd5a8f77a1c3484e4e8e9ee5831e471278e5997f5d51a0",
    "hybrid345/evaluation-seal.json": "974c7403e32d24155971b99d6eee93757bd53986ce81a4adacf89b48e569583b",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _pin(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return {"bytes": path.stat().st_size, "sha256": h.hexdigest()}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _integers(values, name, *, positive=False, upper=None):
    result = []
    for value in values:
        _require(not isinstance(value, (bool, np.bool_)), name + " excludes booleans")
        try:
            item = operator.index(value)
        except TypeError as error:
            raise ValueError(name + " requires integer values") from error
        _require(item >= (1 if positive else 0), name + " outside range")
        _require(upper is None or item < upper, name + " outside range")
        result.append(item)
    _require(len(result) == len(set(result)), name + " must be unique")
    return np.asarray(result, dtype=np.int64)


def reference_classes(reference_root):
    """Import only the six pinned, pure reference modules, without data reads."""
    script_dir = Path(reference_root).resolve() / "scripts"
    _require(len(REFERENCE_CODE_SHA256) == 6, "complete reference-code anchors required")
    for name, digest in REFERENCE_CODE_SHA256.items():
        _require(_pin(script_dir / name)["sha256"] == digest, "reference code drift: " + name)
    modules = ("rec046_common", "rec047_features", "text339_relations",
               "cold_item_features", "final344_adapter", "combination340_models")
    sys.path.insert(0, str(script_dir))
    try:
        loaded = {}
        for name in modules:
            module = importlib.import_module(name)
            _require(Path(module.__file__).resolve() == script_dir / (name + ".py"),
                     "reference module loaded from another location: " + name)
            loaded[name] = module
    finally:
        sys.path.pop(0)
    return loaded["final344_adapter"].MetadataAdapter, loaded["combination340_models"].Trees


@dataclass(frozen=True)
class PredictorAssets:
    metadata_adapter: Any
    gbt: Any
    factor_ids: np.ndarray
    factors: np.ndarray
    train_ids: np.ndarray
    train_counts: np.ndarray
    calibrations: Mapping[int, Mapping[str, tuple[float, float] | None]]
    feature_names: tuple[str, ...]
    provenance: Mapping[str, Any]
    als_reg: float = 0.1


def load_assets(reference_root):
    """Read sealed trained weights/statistics only; never predictions or labels."""
    root = Path(reference_root).resolve()
    base = root / "outputs/recommendation-evidence"
    _require(len(SOURCE_SHA256) >= 7, "complete model-source anchors required")
    provenance = {}
    for relative, digest in SOURCE_SHA256.items():
        actual = _pin(base / relative)
        _require(actual["sha256"] == digest, "source anchor drift: " + relative)
        provenance[relative] = actual
    adapter, trees = reference_classes(root)

    def sealed_file(folder, seal, name):
        actual = _pin(folder / name)
        _require(actual == seal["files"][name], "sealed source drift: " + name)
        provenance[(folder / name).relative_to(base).as_posix()] = actual

    def sealed_directory(folder, seal, prefix):
        expected = {k: v for k, v in seal["files"].items() if k.startswith(prefix + "/")}
        actual = {p.relative_to(folder).as_posix(): _pin(p)
                  for p in (folder / prefix).rglob("*") if p.is_file()}
        _require(expected and actual == expected, "exact model inventory drift: " + prefix)
        provenance[prefix + ":inventory"] = {
            "files": len(actual), "bytes": sum(p["bytes"] for p in actual.values()),
            "sha256": hashlib.sha256(json.dumps(actual, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()}

    old, combo, final, hybrid = [base / x for x in ("text339", "combination340", "final344", "hybrid345")]
    old_seal = _read(old / "prepared-seal.json")
    combo_seal = _read(combo / "fit-seal.json")
    fit_seal = _read(final / "GBT120_s339-seal.json")
    evaluation_seal = _read(hybrid / "evaluation-seal.json")
    _require(fit_seal["input_lock"] == _pin(final / "input-lock.json"), "GBT input-lock parent")
    sealed_file(base / "foundation340", _read(base / "foundation340/prepared-seal.json"),
                "RH/feature-info.json")
    sealed_file(old, old_seal, "catalog.parquet")
    sealed_directory(combo, combo_seal, "ALS/item-factors")
    sealed_directory(final, fit_seal, "GBT120_s339/model/native")
    for name in ("calibration.json", "selection.json"):
        sealed_file(hybrid, evaluation_seal, name)
    selection = _read(hybrid / "selection.json")
    _require(selection["scope"] == "DEVELOPMENT_ONLY" and
             selection["warm"]["content_weight"] == 0 and
             selection["cold"]["head"] == "GBT120_s339", "frozen Qwen0/GBT selection required")
    calibration = _read(hybrid / "calibration.json")
    _require(calibration["scope"] == "DEVELOPMENT_ONLY", "development calibration only")
    calibrations = {}
    for cap in CAPS:
        calibrations[cap] = {}
        for head in ("ALS", "GBT120_s339"):
            rows = [r for r in calibration["fits"] if r["model"] == head and r["cap"] == cap]
            _require(len(rows) == 1, "unique calibration head/cap")
            row = rows[0]
            if row["a"] is None or row["b"] is None:
                _require(cap == 0 and head == "ALS" and row["state"] == "INSUFFICIENT",
                         "unexpected unavailable calibration")
                calibrations[cap][head] = None
            else:
                fit = (float(row["a"]), float(row["b"]))
                _require(np.isfinite(fit).all() and fit[1] >= 0, "finite nonnegative affine")
                calibrations[cap][head] = fit

    catalog = pd.read_parquet(old / "catalog.parquet", columns=["movie_id", "train_count", "blocked"])
    catalog = catalog.sort_values("movie_id")
    _require(catalog.movie_id.is_unique and catalog.train_count.ge(0).all(), "unique train count axis")
    factor_frame = pd.read_parquet(combo / "ALS/item-factors").sort_values("id")
    supported = catalog.train_count.gt(0) & ~catalog.blocked.astype(bool)
    factor_ids = factor_frame.id.to_numpy(np.int64)
    _require(factor_frame.id.is_unique and
             np.array_equal(factor_ids, catalog.loc[supported, "movie_id"].to_numpy(np.int64)),
             "ALS factor identity must equal unblocked training support")
    factors = np.vstack(factor_frame.features).astype(np.float64)
    _require(factors.shape == (45074, 32) and np.isfinite(factors).all(), "frozen ALS32 factors")
    info = _read(base / "foundation340/RH/feature-info.json")
    _require(len(info["names"]) == 230, "RH230 feature-name axis")
    model = trees(final / "GBT120_s339/model/native", np.arange(230))
    _require(len(model.trees) == 120, "fixed GBT120")
    for array in (factor_ids, factors):
        array.flags.writeable = False
    return PredictorAssets(adapter, model, factor_ids, factors,
                           catalog.movie_id.to_numpy(np.int64), catalog.train_count.to_numpy(np.int64),
                           calibrations, tuple(info["names"]), provenance)


class ServicePredictor:
    """Static service metadata/weights; per-request scoring only for candidates."""
    def __init__(self, metadata, assets: PredictorAssets):
        _require(isinstance(metadata, pd.DataFrame) and len(metadata) > 0, "nonempty service metadata")
        _require(set(REQUIRED_METADATA).issubset(metadata), "all normalized RH metadata columns required")
        _require({"mapping_status", "movielens_movie_id"}.issubset(metadata), "explicit MovieLens mapping required")
        self.assets = assets
        self.ids = _integers(metadata.service_movie_id, "service IDs", positive=True)
        self.size = len(self.ids)
        frame = metadata[list(REQUIRED_METADATA)].rename(columns={"service_movie_id": "movie_id"})
        self.metadata = assets.metadata_adapter(frame)
        _require(tuple(self.metadata.names) == assets.feature_names, "exact ordered RH230 feature names")
        self.train_count = np.zeros(self.size, np.int64)
        self.factor_row = np.full(self.size, -1, np.int64)
        matched_rows = np.flatnonzero(metadata.mapping_status.eq("MATCHED").to_numpy())
        matched_values = metadata.movielens_movie_id.iloc[matched_rows].tolist()
        _require(all(not isinstance(v, (bool, np.bool_)) and isinstance(v, (int, np.integer, float, np.floating))
                     and np.isfinite(v) and v > 0 and v == int(v) for v in matched_values),
                 "MATCHED MovieLens IDs must be positive integers")
        matched_ids = np.asarray(matched_values, np.int64)
        _require(len(np.unique(matched_ids)) == len(matched_ids), "unambiguous matched MovieLens axis")
        for source_ids, destination, values in (
            (assets.train_ids, self.train_count, assets.train_counts),
            (assets.factor_ids, self.factor_row, np.arange(len(assets.factor_ids))),
        ):
            _require(np.all(np.diff(source_ids) > 0), "sorted unique source axis")
            positions = np.searchsorted(source_ids, matched_ids)
            good = positions < len(source_ids)
            good[good] &= source_ids[positions[good]] == matched_ids[good]
            destination[matched_rows[good]] = values[positions[good]]
        self.has_factor = self.factor_row >= 0
        _require(np.all(self.train_count[self.has_factor] > 0), "every factor has train support")
        for array in (self.ids, self.train_count, self.factor_row, self.has_factor):
            array.flags.writeable = False

    def _context(self, context):
        cap = context["cap"]
        _require(not isinstance(cap, (bool, np.bool_)) and isinstance(cap, (int, np.integer))
                 and cap in CAPS, "declared history cap required")
        history = _integers(context["oi"], "history indices", upper=self.size)
        viewed = _integers(context.get("viewed", []), "viewed indices", upper=self.size)
        stars = np.asarray(context["stars"], dtype=np.float64)
        _require(stars.ndim == 1 and len(stars) == len(history) <= cap,
                 "actual capped history/rating axes")
        _require(np.isin(stars, np.arange(1, 11) / 2).all(), "exact half-star history")
        return int(cap), history, stars, viewed

    def features(self, context, candidate_indices):
        """Explicit RH230 parity/readout helper; no model inference."""
        _, history, stars, viewed = self._context(context)
        candidates = _integers(candidate_indices, "candidate indices", upper=self.size)
        _require(not np.intersect1d(candidates, np.union1d(history, viewed)).size,
                 "candidates must exclude all viewed/history movies")
        return self.metadata.transform(self.ids[history], stars, self.ids[candidates])

    def predict(self, context, candidate_indices, variant="original"):
        _require(variant in VARIANTS, "declared predictor variant required")
        cap, history, stars, viewed = self._context(context)
        candidates = _integers(candidate_indices, "candidate indices", upper=self.size)
        _require(not np.intersect1d(candidates, np.union1d(history, viewed)).size,
                 "candidates must exclude all viewed/history movies")
        n = len(candidates)
        supported_history = self.has_factor[history]
        nh = int(supported_history.sum())
        available = self.has_factor[candidates] & (nh > 0)
        weight = available.astype(np.float64)
        if variant.startswith("shrink"):
            tau = float(variant[6:])
            count = self.train_count[candidates].astype(np.float64)
            weight *= count / (count + tau)
        elif variant == "min20":
            weight *= self.train_count[candidates] >= 20
        als_positions = np.flatnonzero(weight > 0)
        gbt_positions = np.flatnonzero(weight < 1)
        raw_als = np.full(n, np.nan)
        raw_gbt = np.full(n, np.nan)
        cal_als = np.full(n, np.nan)
        cal_gbt = np.full(n, np.nan)
        fits = self.assets.calibrations[cap]
        if len(als_positions):
            fit = fits["ALS"]
            _require(fit is not None and nh > 0, "active calibrated ALS requires supported history")
            factors = self.assets.factors
            y = factors[self.factor_row[history[supported_history]]]
            user = np.linalg.solve(y.T @ y + self.assets.als_reg * nh * np.eye(y.shape[1]),
                                   y.T @ stars[supported_history])
            raw_als[als_positions] = factors[self.factor_row[candidates[als_positions]]] @ user
            cal_als[als_positions] = fit[0] + fit[1] * raw_als[als_positions]
        if len(gbt_positions):
            x = self.metadata.transform(self.ids[history], stars, self.ids[candidates[gbt_positions]])
            values = np.asarray(self.assets.gbt.predict(x), dtype=np.float64)
            _require(values.shape == (len(gbt_positions),) and np.isfinite(values).all(), "finite actual GBT output")
            raw_gbt[gbt_positions] = values
            fit = fits["GBT120_s339"]
            _require(fit is not None, "complete GBT calibration")
            cal_gbt[gbt_positions] = fit[0] + fit[1] * values
        prediction = np.zeros(n, dtype=np.float64)
        prediction[als_positions] += weight[als_positions] * cal_als[als_positions]
        prediction[gbt_positions] += (1 - weight[gbt_positions]) * cal_gbt[gbt_positions]
        _require(np.isfinite(prediction).all(), "finite candidate predictions")
        branch = np.full(n, "GBT", dtype="<U16")
        branch[weight == 1] = "ALS"
        branch[(weight > 0) & (weight < 1)] = "ALS_GBT_SHRINK"
        return {
            "candidate_indices": candidates, "service_movie_id": self.ids[candidates],
            "prediction": prediction, "rating_clipped": np.clip(prediction, .5, 5),
            "raw_als": raw_als, "raw_gbt": raw_gbt, "calibrated_als": cal_als,
            "calibrated_gbt": cal_gbt, "als_weight": weight, "branch": branch,
            "train_count": self.train_count[candidates], "has_factor": self.has_factor[candidates],
            "als_available": available, "variant": variant, "cap": cap,
            "actual_history": len(history), "supported_history": nh,
            "candidate_count": n, "als_rows_computed": len(als_positions),
            "gbt_rows_computed": len(gbt_positions),
            "scope": "DEVELOPMENT_ONLY_CURRENT_SERVICE_METADATA",
        }
