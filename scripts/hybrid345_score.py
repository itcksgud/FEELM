"""Label-free Qwen-to-ALS mapping and fixed-context scoring for hybrid345.

This module deliberately has no labels path.  It creates scores and explicit
availability only; calibration and evaluation are a later, separately reviewed
stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

import psutil

from hybrid345_common import (
    PRELABEL_REVIEW,
    prelabel_fingerprint,
    require_prelabel_review,
)

from hybrid345_models import (
    dense_profile_scores,
    factor_reconstruction,
    fold_in_scores,
    ridge_from_statistics,
    ridge_sufficient_statistics,
    sparse_profile_scores,
    stratified_mapper_split,
)


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/hybrid345"
OUT = ROOT / "outputs/recommendation-evidence/hybrid345"
BASE = ROOT / "outputs/recommendation-evidence"
NAMES = np.array([
    "ALS", "STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT",
    "FM150_s339", "FM150_s344", "FM150_s345",
    "GBT120_s339", "GBT120_s344", "GBT120_s345", "ALS_C2F",
])


class ResourceBudget:
    """Continuously sample this process and fail closed on a declared budget."""

    def __init__(self, seconds, rss_bytes, label):
        self.seconds_limit = float(seconds)
        self.rss_limit = int(rss_bytes)
        self.label = str(label)
        self.started = None
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.started = time.perf_counter()
        process = psutil.Process(os.getpid())
        self.peak_rss_bytes = int(process.memory_info().rss)
        self.check()

        def sample():
            while not self._stop.is_set():
                try:
                    self.peak_rss_bytes = max(
                        self.peak_rss_bytes, int(process.memory_info().rss)
                    )
                except psutil.Error:
                    pass
                self._stop.wait(0.05)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def check(self):
        require(self.started is not None, self.label + " resource monitor is not started")
        elapsed = time.perf_counter() - self.started
        require(elapsed <= self.seconds_limit, self.label + " exceeded time limit")
        if self.peak_rss_bytes:
            require(self.peak_rss_bytes <= self.rss_limit, self.label + " exceeded host memory limit")
        return elapsed

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        elapsed = self.check()
        return {
            "seconds": elapsed,
            "process_peak_rss_bytes": int(self.peak_rss_bytes),
            "seconds_limit": self.seconds_limit,
            "host_memory_bytes_limit": self.rss_limit,
        }


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def config():
    return json.loads((DOC / "config.json").read_text(encoding="utf-8"))


def verify_source_pins(include_evaluation_labels=False):
    cfg = config()
    label_path = cfg["sources"]["evaluation_labels"]
    checked = {}
    for name, expected in cfg["source_pins"].items():
        if name == label_path and not include_evaluation_labels:
            continue
        actual = pin(ROOT / name)
        require(actual == expected, "pinned source drift: " + name)
        checked[name] = actual
    return checked


def execution_lineage():
    return {
        "implementation": prelabel_fingerprint(),
        "independent_review": pin(PRELABEL_REVIEW),
    }


def verify_als_factor_source():
    """Bind every Spark factor part, including CRC files, to the fit seal."""
    cfg = config()
    seal_path = ROOT / cfg["sources"]["combination_fit_seal"]
    expected_seal_pin = cfg["source_pins"][cfg["sources"]["combination_fit_seal"]]
    require(pin(seal_path) == expected_seal_pin, "combination340 fit seal drift")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    prefix = "ALS/item-factors/"
    expected = {name: value for name, value in seal.get("files", {}).items()
                if name.startswith(prefix)}
    require(expected, "fit seal has no ALS item-factor files")
    directory = ROOT / cfg["sources"]["als_factors"]
    actual_names = {
        prefix + path.relative_to(directory).as_posix()
        for path in directory.rglob("*") if path.is_file()
    }
    require(actual_names == set(expected), "ALS item-factor inventory differs from fit seal")
    for name, expected_pin in expected.items():
        require(pin(BASE / "combination340" / name) == expected_pin,
                "ALS item-factor file differs from fit seal: " + name)
    return {
        "fit_seal": pin(seal_path),
        "directory": str(directory.relative_to(ROOT)).replace("\\", "/"),
        "file_count": len(expected),
        "files": expected,
    }


def prepare_prior():
    """Create a user-equal ten-bin prior from the exact ACTUAL_ALS train rows."""
    require_prelabel_review()
    cfg = config()
    OUT.mkdir(parents=True, exist_ok=True)
    prior_path, seal_path = OUT / "current-prior.npz", OUT / "prior-seal.json"
    require(not prior_path.exists() and not seal_path.exists(), "preserve current training prior")
    verified = verify_source_pins(False)
    source = ROOT / cfg["sources"]["training_ratings"]
    frame = pd.read_parquet(source, columns=["uid", "rating"])
    population = cfg["profile"]["prior_population"]
    require(len(frame) == population["rows"] and frame.uid.nunique() == population["users"],
            "current training prior population")
    values = frame.rating.to_numpy(np.float64)
    indices = np.rint(values * 2 - 1).astype(np.int64)
    require(np.isfinite(values).all() and np.allclose(values, (indices + 1) / 2) and
            ((indices >= 0) & (indices < 10)).all(), "half-star training prior inputs")
    users, inverse = np.unique(frame.uid.to_numpy(np.int64), return_inverse=True)
    histograms = np.zeros((len(users), 10), dtype=np.int64)
    np.add.at(histograms, (inverse, indices), 1)
    totals = histograms.sum(axis=1)
    require((totals > 0).all() and int(totals.sum()) == len(frame), "complete per-user histograms")
    pi0 = (histograms / totals[:, None]).mean(axis=0)
    pi0 /= pi0.sum()
    g0_mid = np.cumsum(pi0) - 0.5 * pi0
    require(np.isfinite(g0_mid).all() and np.all(np.diff(g0_mid) > 0), "valid mid-rank prior")
    np.savez_compressed(prior_path, pi0=pi0, g0_mid=g0_mid, users=users,
                        histograms=histograms)
    write_json(seal_path, {
        "scope": cfg["claim_scope"], "file": pin(prior_path), "source": pin(source),
        "rows": len(frame), "users": len(users), "weighting": "USER_EQUAL",
        "pi0": pi0.tolist(), "g0_mid": g0_mid.tolist(), "labels_opened": False,
        "verified_source_count": len(verified), **execution_lineage(),
    })
    print("HYBRID345_PRIOR_SEALED", len(users), len(frame), flush=True)


def verify_prior():
    cfg = config()
    record = json.loads((OUT / "prior-seal.json").read_text(encoding="utf-8"))
    require(record["source"] == pin(ROOT / cfg["sources"]["training_ratings"]),
            "same training prior source")
    require(record["file"] == pin(OUT / "current-prior.npz"), "sealed current prior")
    require(record.get("implementation") == prelabel_fingerprint(),
            "prior implementation drift")
    require(record.get("independent_review") == pin(PRELABEL_REVIEW),
            "prior review gate drift")
    return record


def load_catalog():
    cfg = config()
    catalog = pd.read_parquet(ROOT / cfg["sources"]["catalog"])
    ids = catalog.movie_id.to_numpy(np.int64)
    require(len(ids) == cfg["catalog_movies"] and catalog.movie_id.is_unique,
            "canonical unique catalog size")
    require(np.array_equal(ids, np.sort(ids)), "canonical catalog order")
    actual = (~catalog.blocked.to_numpy(bool)) & catalog.train_count.to_numpy(np.int64).__gt__(0)
    require(int(actual.sum()) == cfg["movie_groups"]["W_DIRECT"], "actual ALS support count")
    return catalog, ids, actual


def load_actual_factors(ids, expected):
    cfg = config()
    frame = pd.read_parquet(ROOT / cfg["sources"]["als_factors"])
    require(frame.id.is_unique, "unique ALS factor movie IDs")
    factor_ids = frame.id.to_numpy(np.int64)
    require(np.array_equal(np.sort(factor_ids), ids[expected]), "ALS factor support identity")
    result = np.full((len(ids), 32), np.nan, dtype=np.float32)
    positions = np.searchsorted(ids, factor_ids)
    result[positions] = np.vstack(frame.features).astype(np.float32)
    require(np.isfinite(result[expected]).all() and np.isnan(result[~expected]).all(),
            "explicit ALS factor availability")
    return result


def verify_embeddings(path, expected_rows, expected_dim):
    values = np.load(path, mmap_mode="r")
    require(values.shape == (expected_rows, expected_dim) and values.dtype == np.float32,
            "Qwen embedding shape and dtype")
    require(np.isfinite(values).all(), "finite Qwen embeddings")
    norms = np.linalg.norm(values, axis=1)
    require(float(np.max(np.abs(norms - 1))) <= 2e-5, "L2-normalized Qwen embeddings")
    return values, {"norm_min": float(norms.min()), "norm_max": float(norms.max()),
                    "max_norm_error": float(np.max(np.abs(norms - 1)))}


def verify_qwen_parent(embedding_path, catalog_ids):
    from hybrid345_encode import verify_full

    seal_path = OUT / "qwen-embedding-seal.json"
    record = verify_full()
    require(record.get("status") == "PASS", "passing Qwen embedding seal")
    for name, expected in record["artifacts"].items():
        require(pin(OUT / name) == expected, "sealed Qwen artifact " + name)
    ids = np.load(OUT / "qwen-movie-ids.npy", allow_pickle=False)
    require(np.array_equal(ids.astype(np.int64), np.asarray(catalog_ids, dtype=np.int64)),
            "Qwen and canonical movie axes")
    require(Path(embedding_path).resolve() == (OUT / "qwen-embeddings.npy").resolve(),
            "scoring must use the sealed Qwen matrix")
    return record


def mapper_metrics_by_stratum(target, prediction, strata):
    result = {"ALL": factor_reconstruction(target, prediction)}
    for code, name in enumerate(("SUPPORT_1_9", "SUPPORT_10_49", "SUPPORT_50_PLUS")):
        mask = strata == code
        result[name] = factor_reconstruction(target[mask], prediction[mask])
    return result


def choose_mapper(records):
    require(records, "mapper candidates")
    require(all(r["validation"]["ALL"]["mean_cosine"] is not None for r in records),
            "finite mapper validation cosine")
    return min(records, key=lambda r: (
        -r["validation"]["ALL"]["mean_cosine"],
        r["validation"]["ALL"]["rmse"],
        r["alpha"],
    ))


def map_factors(embedding_path):
    require_prelabel_review()
    cfg = config()
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [OUT / name for name in ("mapper.npz", "mapped-factors.npy", "mapper-selection.json",
                                       "mapper-seal.json")]
    require(not any(path.exists() for path in targets), "preserve mapper artifacts")
    budget = ResourceBudget(
        cfg["resource_limits"]["mapper_seconds"],
        cfg["resource_limits"]["host_memory_bytes"],
        "Qwen-to-ALS mapper",
    ).start()
    mapped_path = OUT / "mapped-factors.npy"
    partial_mapped = OUT / ".mapped-factors.partial.npy"
    require(not partial_mapped.exists(), "stale partial mapped factors")
    try:
        source_pins = verify_source_pins(False)
        catalog, ids, actual = load_catalog()
        qwen_parent = verify_qwen_parent(embedding_path, ids)
        x, embedding_audit = verify_embeddings(embedding_path, len(ids), cfg["qwen"]["dimension"])
        als_factor_parent = verify_als_factor_source()
        factors = load_actual_factors(ids, actual)
        support = catalog.train_count.to_numpy(np.int64)[actual]
        train, validation, strata = stratified_mapper_split(
            ids[actual], support, cfg["mapper"]["split_salt"], cfg["mapper"]["train_fraction"])
        require(int(train.sum() + validation.sum()) == int(actual.sum()), "complete mapper split")

        x_actual = np.asarray(x[actual], dtype=np.float64)
        y_actual = factors[actual].astype(np.float64)
        statistics = ridge_sufficient_statistics(x_actual[train], y_actual[train])
        records = []
        for alpha in cfg["mapper"]["alphas"]:
            coefficient, intercept = ridge_from_statistics(*statistics, float(alpha))
            predicted = x_actual[validation] @ coefficient + intercept
            records.append({"alpha": float(alpha), "validation": mapper_metrics_by_stratum(
                y_actual[validation], predicted, strata[validation])})
            budget.check()
        selected = choose_mapper(records)
        require(selected["validation"]["ALL"]["mean_cosine"] > 0,
                "positive held-out factor reconstruction cosine")

        final_statistics = ridge_sufficient_statistics(x_actual, y_actual)
        coefficient, intercept = ridge_from_statistics(*final_statistics, selected["alpha"])
        mapped = np.lib.format.open_memmap(partial_mapped, mode="w+", dtype=np.float32,
                                           shape=(len(ids), y_actual.shape[1]))
        for start in range(0, len(ids), 4096):
            stop = min(start + 4096, len(ids))
            mapped[start:stop] = np.asarray(x[start:stop], np.float64) @ coefficient + intercept
            budget.check()
        mapped.flush()
        require(np.isfinite(mapped).all(), "finite mapped factors")
        del mapped
        resources = budget.stop()
        os.replace(partial_mapped, mapped_path)
    except Exception:
        budget._stop.set()
        if budget._thread is not None:
            budget._thread.join(timeout=1.0)
        partial_mapped.unlink(missing_ok=True)
        raise
    np.savez_compressed(OUT / "mapper.npz", coefficient=coefficient, intercept=intercept,
                        alpha=np.array(selected["alpha"]), teacher_movie_ids=ids[actual],
                        train_mask=train, validation_mask=validation, strata=strata)
    selection = {
        "scope": cfg["claim_scope"], "selected_alpha": selected["alpha"],
        "selection_order": cfg["mapper"]["selection"], "candidates": records,
        "split": {"teacher_items": int(actual.sum()), "train_items": int(train.sum()),
                  "validation_items": int(validation.sum()),
                  "strata": {name: {"all": int((strata == code).sum()),
                                     "train": int((train & (strata == code)).sum()),
                                     "validation": int((validation & (strata == code)).sum())}
                              for code, name in enumerate(("SUPPORT_1_9", "SUPPORT_10_49", "SUPPORT_50_PLUS"))}},
        "embedding_audit": embedding_audit, "labels_opened": False,
        "resources": resources,
    }
    write_json(OUT / "mapper-selection.json", selection)
    files = {name: pin(OUT / name) for name in ("mapper.npz", "mapped-factors.npy",
                                                 "mapper-selection.json")}
    write_json(OUT / "mapper-seal.json", {
        "scope": cfg["claim_scope"], "files": files,
        "sources": {"config": pin(DOC / "config.json"), "embedding": pin(embedding_path)},
        "qwen_parent": pin(OUT / "qwen-embedding-seal.json"),
        "als_factor_parent": als_factor_parent,
        "labels_opened": False, "verified_source_count": len(source_pins),
        **execution_lineage(),
    })
    print("HYBRID345_MAPPER_SEALED", selected["alpha"], round(resources["seconds"], 2), flush=True)


def verify_mapper(embedding_path):
    record = json.loads((OUT / "mapper-seal.json").read_text(encoding="utf-8"))
    require(record["sources"]["config"] == pin(DOC / "config.json"), "same mapper config")
    require(record["sources"]["embedding"] == pin(embedding_path), "same mapper embedding")
    require(record["qwen_parent"] == pin(OUT / "qwen-embedding-seal.json"),
            "same mapper Qwen parent")
    for name, expected in record["files"].items():
        require(pin(OUT / name) == expected, "sealed mapper artifact " + name)
    require(record.get("implementation") == prelabel_fingerprint(),
            "mapper implementation drift")
    require(record.get("independent_review") == pin(PRELABEL_REVIEW),
            "mapper review gate drift")
    require(record.get("als_factor_parent") == verify_als_factor_source(),
            "mapper ALS factor parent drift")
    return record


def score(embedding_path):
    require_prelabel_review()
    cfg = config()
    targets = [OUT / name for name in ("predictions.npz", "availability.csv", "prediction-seal.json")]
    require(not any(path.exists() for path in targets), "preserve prediction artifacts")
    budget = ResourceBudget(
        cfg["resource_limits"]["scoring_seconds"],
        cfg["resource_limits"]["host_memory_bytes"],
        "fixed-context scoring",
    ).start()
    source_pins = verify_source_pins(False)
    mapper_parent = verify_mapper(embedding_path)
    prior_parent = verify_prior()
    catalog, ids, actual_support = load_catalog()
    qwen_parent = verify_qwen_parent(embedding_path, ids)
    qwen, qwen_audit = verify_embeddings(embedding_path, len(ids), cfg["qwen"]["dimension"])
    e5 = np.load(ROOT / cfg["sources"]["e5"], mmap_mode="r")
    require(e5.shape == (len(ids), 384) and e5.dtype == np.float32 and np.isfinite(e5).all(),
            "fixed E5 axis")
    structure = sparse.load_npz(ROOT / cfg["sources"]["structured"]).tocsr()
    require(structure.shape[0] == len(ids) and np.isfinite(structure.data).all(),
            "fixed structured axis")
    prior_file = np.load(ROOT / cfg["sources"]["prior"])
    prior = prior_file["g0_mid"].astype(np.float64)
    als_factor_parent = verify_als_factor_source()
    actual_factors = load_actual_factors(ids, actual_support)
    mapped = np.load(OUT / "mapped-factors.npy", mmap_mode="r")
    require(mapped.shape == (len(ids), 32) and np.isfinite(mapped).all(), "mapped factor axis")

    contexts = json.loads((ROOT / cfg["sources"]["contexts"]).read_text(encoding="utf-8"))
    require(len(contexts) == 270 * len(cfg["caps"]), "fixed context count")
    rows = max(int(c["stop"]) for c in contexts)
    require(rows == cfg["context_rows"], "fixed score row count")
    values = np.full((rows, len(NAMES)), np.nan, dtype=np.float64)
    available = np.zeros((rows, len(NAMES)), dtype=bool)

    old = np.load(ROOT / cfg["sources"]["als_predictions"])
    old_names = old["names"].tolist()
    als = old["predictions"][:, old_names.index("ACTUAL_ALS")]
    als_available = old["actual_direct"].astype(bool)
    require(als.shape == (rows,) and np.array_equal(np.isfinite(als), als_available),
            "explicit reused ALS availability")
    values[:, 0] = als
    available[:, 0] = als_available
    for family, source_key in (("FM150", "fm_predictions_by_seed"),
                               ("GBT120", "gbt_predictions_by_seed")):
        for seed, relative in cfg["sources"][source_key].items():
            name = family + "_s" + seed
            column = int(np.flatnonzero(NAMES == name)[0])
            prediction = np.load(ROOT / relative)
            require(prediction.shape == (rows,) and np.isfinite(prediction).all(),
                    name + " fixed predictions")
            values[:, column] = prediction
            available[:, column] = True

    availability_rows = []
    for number, context in enumerate(contexts):
        history = np.asarray(context["oi"], dtype=np.int64)
        stars = np.asarray(context["stars"], dtype=np.float64)
        candidates = np.asarray(context["ei"], dtype=np.int64)
        sl = slice(int(context["start"]), int(context["stop"]))
        require(sl.stop - sl.start == len(candidates), "context row slice")
        require(len(history) == len(stars) == int(context["h"]), "history rating axis")
        require(not np.intersect1d(history, candidates).size, "history and targets disjoint")
        generated = {}
        generated["STRUCTURED_DIRECT"] = sparse_profile_scores(
            structure, history, stars, candidates, prior)
        generated["E5_DIRECT"] = dense_profile_scores(e5, history, stars, candidates, prior)
        generated["QWEN_DIRECT"] = dense_profile_scores(qwen, history, stars, candidates, prior)
        history_supported = actual_support[history]
        generated["ALS_C2F"] = fold_in_scores(
            actual_factors[history[history_supported]], stars[history_supported], mapped[candidates],
            reg=cfg["als_fold_in_reg"])
        for name, (prediction, active) in generated.items():
            column = int(np.flatnonzero(NAMES == name)[0])
            if active:
                require(np.isfinite(prediction).all(), "finite active " + name)
                values[sl, column] = prediction
                available[sl, column] = True
            else:
                require(np.isnan(prediction).all(), "inactive is N/A " + name)
            availability_rows.append({
                "context_index": number, "uid": int(context["uid"]), "cap": int(context["cap"]),
                "h": int(context["h"]), "model": name, "available": bool(active),
                "target_rows": len(candidates), "actual_als_history": int(history_supported.sum()),
            })
        budget.check()
    require(np.array_equal(np.isfinite(values), available), "prediction availability equals finiteness")
    require(np.array_equal(values[:, 0], als, equal_nan=True), "ALS values preserved exactly")
    np.savez_compressed(OUT / "predictions.npz", names=NAMES, predictions=values,
                        availability=available)
    pd.DataFrame(availability_rows).to_csv(OUT / "availability.csv", index=False)
    resources = budget.stop()
    sources = {
        "config": pin(DOC / "config.json"), "embedding": pin(embedding_path),
        "mapper_seal": pin(OUT / "mapper-seal.json"),
        "catalog": pin(ROOT / cfg["sources"]["catalog"]),
        "contexts": pin(ROOT / cfg["sources"]["contexts"]),
        "structured": pin(ROOT / cfg["sources"]["structured"]),
        "e5": pin(ROOT / cfg["sources"]["e5"]),
        "prior": pin(ROOT / cfg["sources"]["prior"]),
        "als_predictions": pin(ROOT / cfg["sources"]["als_predictions"]),
        "fm_predictions": {seed: pin(ROOT / relative) for seed, relative in
                           cfg["sources"]["fm_predictions_by_seed"].items()},
        "gbt_predictions": {seed: pin(ROOT / relative) for seed, relative in
                            cfg["sources"]["gbt_predictions_by_seed"].items()},
    }
    write_json(OUT / "prediction-seal.json", {
        "scope": cfg["claim_scope"], "names": NAMES.tolist(), "rows": rows,
        "files": {name: pin(OUT / name) for name in ("predictions.npz", "availability.csv")},
        "sources": sources, "mapper_parent": mapper_parent, "qwen_audit": qwen_audit,
        "labels_opened": False, "resources": resources,
        "prior_parent": prior_parent, "qwen_parent": qwen_parent,
        "als_factor_parent": als_factor_parent,
        "verified_source_count": len(source_pins), **execution_lineage(),
    })
    print("HYBRID345_PREDICTIONS_SEALED", rows, round(resources["seconds"], 2), flush=True)


def verify_predictions(embedding_path=OUT / "qwen-embeddings.npy"):
    """Recursively verify every label-free parent used by the score matrix."""
    require_prelabel_review()
    cfg = config()
    record = json.loads((OUT / "prediction-seal.json").read_text(encoding="utf-8"))
    require(record.get("scope") == cfg["claim_scope"], "prediction scope drift")
    require(record.get("names") == NAMES.tolist(), "prediction column axis drift")
    require(record.get("rows") == cfg["context_rows"], "prediction row axis drift")
    require(record.get("labels_opened") is False, "prediction stage opened labels")
    require(record.get("implementation") == prelabel_fingerprint(),
            "prediction implementation drift")
    require(record.get("independent_review") == pin(PRELABEL_REVIEW),
            "prediction review gate drift")
    for name, expected in record.get("files", {}).items():
        require(pin(OUT / name) == expected, "sealed prediction artifact " + name)
    catalog, ids, _actual = load_catalog()
    del catalog
    require(record.get("prior_parent") == verify_prior(), "prediction prior parent drift")
    require(record.get("mapper_parent") == verify_mapper(embedding_path),
            "prediction mapper parent drift")
    require(record.get("qwen_parent") == verify_qwen_parent(embedding_path, ids),
            "prediction Qwen parent drift")
    require(record.get("als_factor_parent") == verify_als_factor_source(),
            "prediction ALS factor parent drift")
    current_sources = {
        "config": pin(DOC / "config.json"), "embedding": pin(embedding_path),
        "mapper_seal": pin(OUT / "mapper-seal.json"),
        "catalog": pin(ROOT / cfg["sources"]["catalog"]),
        "contexts": pin(ROOT / cfg["sources"]["contexts"]),
        "structured": pin(ROOT / cfg["sources"]["structured"]),
        "e5": pin(ROOT / cfg["sources"]["e5"]),
        "prior": pin(ROOT / cfg["sources"]["prior"]),
        "als_predictions": pin(ROOT / cfg["sources"]["als_predictions"]),
        "fm_predictions": {seed: pin(ROOT / relative) for seed, relative in
                           cfg["sources"]["fm_predictions_by_seed"].items()},
        "gbt_predictions": {seed: pin(ROOT / relative) for seed, relative in
                            cfg["sources"]["gbt_predictions_by_seed"].items()},
    }
    require(record.get("sources") == current_sources, "prediction source lineage drift")
    bundle = np.load(OUT / "predictions.npz", allow_pickle=False)
    require(bundle["names"].tolist() == NAMES.tolist(), "prediction file column axis drift")
    values, availability = bundle["predictions"], bundle["availability"]
    require(values.shape == (cfg["context_rows"], len(NAMES)), "prediction file shape drift")
    require(np.array_equal(np.isfinite(values), availability),
            "prediction file availability drift")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prior", "map", "score"))
    parser.add_argument("--embedding", type=Path,
                        default=OUT / "qwen-embeddings.npy")
    args = parser.parse_args()
    embedding = args.embedding.resolve()
    if args.action == "prior":
        prepare_prior()
        return
    require(embedding.is_file(), "Qwen embedding file exists")
    map_factors(embedding) if args.action == "map" else score(embedding)


if __name__ == "__main__":
    main()
