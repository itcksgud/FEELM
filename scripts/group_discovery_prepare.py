"""Reviewed, staged preparation of immutable movie groups. No rating model."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import threading
import time
import warnings

import numpy as np
import pandas as pd
import psutil
from scipy import sparse
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score
from threadpoolctl import threadpool_limits

from group_discovery_geometry import (anchor_distance, canonical_model, compare_user_groups,
                                      geometry, midrank, movie_vectors, require)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/group-discovery-preparation"
OUT = ROOT / "outputs/recommendation-evidence/group-discovery-preparation"
FILES = [DOC / "EXECUTION.md", DOC / "config.json", Path(__file__),
         ROOT / "scripts/group_discovery_geometry.py", ROOT / "scripts/test_group_discovery_geometry.py"]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, obj):
    require(not path.exists(), "preserve output " + str(path))
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def pin(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def fingerprint():
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in FILES}


def approved(stage):
    review = read_json(DOC / "execution-review.json")
    require(review["status"] == "PASS" and stage in review["authorized_stages"], "stage review required")
    require(review["fingerprint"] == fingerprint(), "review exact code and design")
    return review["fingerprint"]


def verify_stage(stage):
    directory = OUT / stage
    record = read_json(directory / "seal.json")
    require(record["fingerprint"] == fingerprint(), "stage code identity")
    for name, expected in record["files"].items():
        require(pin(directory / name) == expected, "stage artifact identity " + name)
    review = read_json(DOC / (stage + "-result-review.json"))
    require(review["status"] == "PASS" and review["seal"] == pin(directory / "seal.json"), "independent stage result review")
    return record


def seal(stage, extra, approved_fingerprint):
    directory = OUT / stage
    files = {p.relative_to(directory).as_posix(): pin(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    require(fingerprint() == approved_fingerprint, "code changed before final seal")
    write_json(directory / "seal.json", {"fingerprint": approved_fingerprint, "files": files, **extra})


class Guard:
    def __init__(self, cfg, stage):
        self.cfg, self.stage = cfg, stage
        self.started = time.monotonic()
        self.peak = 0
        self.minimum_free = psutil.virtual_memory().available
        self.done = threading.Event()

    def start(self):
        require(self.minimum_free >= self.cfg["start_free_gib"] * 2**30, "host memory start gate")
        def monitor():
            strikes = 0
            proc = psutil.Process()
            def stop(reason, rss=None, free=None):
                record = {"reason": reason, "stage": self.stage, "rss": rss, "host_available": free,
                          "seconds": time.monotonic() - self.started, "exit_code": 86}
                try:
                    write_json(OUT / self.stage / "resource-stop.json", record)
                finally:
                    print("RESOURCE_STOP", json.dumps(record), flush=True)
                    os._exit(86)
            while not self.done.wait(1):
                try:
                    rss = proc.memory_info().rss
                    for child in proc.children(recursive=True):
                        try:
                            rss += child.memory_info().rss
                        except psutil.NoSuchProcess:
                            pass
                    free = psutil.virtual_memory().available
                except Exception as exc:
                    stop("watchdog_error:" + type(exc).__name__)
                self.peak, self.minimum_free = max(self.peak, rss), min(self.minimum_free, free)
                violation = rss > self.cfg["rss_limit_gib"] * 2**30 or free < self.cfg["stop_free_gib"] * 2**30
                strikes = strikes + 1 if violation else 0
                if strikes >= 3 or time.monotonic() - self.started > self.cfg["timeouts"][self.stage]:
                    stop("memory" if strikes >= 3 else "timeout", rss, free)
        threading.Thread(target=monitor, daemon=True).start()

    def result(self):
        self.peak = max(self.peak, psutil.Process().memory_info().rss)
        return {"seconds": time.monotonic() - self.started, "peak_rss": self.peak,
                "minimum_host_available": self.minimum_free}


def prepare(cfg):
    base = Path(cfg["source_root"]) / "outputs/recommendation-evidence"
    for name, expected in cfg["sources"].items():
        require(pin(base / name) == expected, "source integrity " + name)
    meta = pd.read_parquet(base / "rec-ev-045/metadata.parquet", columns=["movie_id", "tmdb_id", "genre_ids", "keyword_ids"])
    named = pd.read_parquet(base / "rec-ev-033/metadata.parquet", columns=["movie_id", "tmdb_id", "title", "genre_ids", "genre_names", "keyword_ids", "keyword_names"])
    cat = pd.read_parquet(base / "text339/catalog.parquet", columns=["movie_id"])
    dates = pd.read_parquet(base / "text339/texts.parquet", columns=["movie_id", "release_date"])
    ids = meta.movie_id.to_numpy(np.int64)
    require(len(ids) == cfg["expected_movies"] and np.all(np.diff(ids) > 0), "canonical movie axis")
    for table in [named, cat, dates]:
        require(np.array_equal(ids, table.movie_id), "same movie row order")
    require(np.array_equal(meta.tmdb_id, named.tmdb_id), "same TMDB identity")
    contexts = [c for c in read_json(base / "text339/contexts.json") if c["cap"] == 10]
    require(len(contexts) == cfg["expected_contexts"] and len({c["uid"] for c in contexts}) == len(contexts), "context population")
    require(sum(c["h"] > 0 for c in contexts) == cfg["expected_nonempty"], "history population")
    minimal = []
    for c in sorted(contexts, key=lambda c: c["uid"]):
        oi, viewed = np.asarray(c["oi"], int), np.asarray(c["viewed"], int)
        stars, timestamps = np.asarray(c["stars"], float), np.asarray(c["input_timestamps"], int)
        for axis in [oi, viewed]:
            require(len(np.unique(axis)) == len(axis) and ((axis >= 0) & (axis < len(ids))).all(), "unique valid history index")
        require(len(oi) == len(stars) == len(timestamps) == c["h"] == min(10, len(viewed)), "input cap")
        require(np.isin(oi, viewed).all() and (timestamps < cfg["origin"]).all(), "legal input time and subset")
        require(np.isin(stars, np.arange(1, 11) / 2).all(), "actual permitted input stars")
        minimal.append({key: c[key] for key in ["uid", "h", "oi", "stars", "viewed"]})
    x, valid, vocabulary = movie_vectors(meta.genre_ids, meta.keyword_ids)
    d = OUT / "prepare"
    sparse.save_npz(d / "features.npz", x)
    np.savez_compressed(d / "axis.npz", movie_ids=ids, valid=valid)
    write_json(d / "vocabulary.json", vocabulary)
    write_json(d / "contexts.json", minimal)
    dates_utc = pd.to_datetime(dates.release_date, format="%Y-%m-%d", errors="coerce", utc=True)
    available = dates_utc.notna().to_numpy() & (dates_utc.astype("int64").to_numpy() // 10**9 <= cfg["catalog_snapshot_timestamp"])
    pd.DataFrame({"movie_id": ids, "title": named.title, "available": available}).to_parquet(d / "movies.parquet", index=False)
    names = {}
    for prefix in ["genre", "keyword"]:
        for row_ids, row_names in zip(named[prefix + "_ids"], named[prefix + "_names"]):
            require(len(row_ids) == len(row_names), "aligned names")
            for value, name in zip(row_ids, row_names):
                names.setdefault(prefix + ":" + str(int(value)), str(name))
    write_json(d / "names.json", names)
    report = {"movies": len(ids), "valid_geometry": int(valid.sum()), "dimensions": x.shape[1],
              "genre_dimensions": len(vocabulary["genre_ids"]), "keyword_dimensions": len(vocabulary["keyword_ids"]),
              "nonzero": int(x.nnz), "contexts": len(minimal), "nonempty_inputs": sum(c["h"] > 0 for c in minimal),
              "keyword_supported": int(sum(np.asarray(vocabulary["block_counts"]) == 2)),
              "future_rating_values_read": 0, "predicted_ratings_read": 0, "training_ratings_read": 0,
              "sources": cfg["sources"], "python": platform.python_version()}
    write_json(d / "report.json", report)
    print("PREPARED", json.dumps({k: report[k] for k in ["movies", "valid_geometry", "dimensions", "contexts"]}), flush=True)
    return {"status": "PREPARED"}


def fit(cfg):
    verify_stage("prepare")
    p, d = OUT / "prepare", OUT / "fit"
    full = sparse.load_npz(p / "features.npz")
    with np.load(p / "axis.npz") as axis:
        ids, valid = axis["movie_ids"], axis["valid"]
    x = full[valid]
    runs, kvals = [], []
    for k in cfg["k_values"]:
        labels_for_k, passed = [], True
        for seed in cfg["seeds"]:
            start = time.monotonic()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model = KMeans(n_clusters=k, init="k-means++", n_init=cfg["n_init"],
                               max_iter=cfg["max_iter"], tol=cfg["tol"], algorithm="lloyd", random_state=seed).fit(x)
            failures = ["warning:" + str(w.message) for w in caught]
            centers, labels = model.cluster_centers_, model.labels_
            try:
                centers, labels = canonical_model(x, centers, labels, ids[valid])
                norms = np.linalg.norm(centers, axis=1)
                require((norms > 0).all(), "nonzero centers")
                # Check distinct centers without allocating K x K x dimensions.
                for g in range(k):
                    require((np.linalg.norm(centers[g + 1:] - centers[g], axis=1) > 1e-10).all(), "distinct centers")
            except ValueError as exc:
                failures.append(str(exc))
            count = np.bincount(labels, minlength=k)
            if count.min() < cfg["min_group_size"]:
                failures.append("minimum_group_size")
            if count.max() / len(labels) > cfg["max_group_share"]:
                failures.append("maximum_group_share")
            if model.n_iter_ >= cfg["max_iter"]:
                failures.append("iteration_limit")
            passed &= not failures
            labels_for_k.append(labels.copy())
            np.savez_compressed(d / f"k{k}-seed{seed}.npz", centers=centers, labels=labels)
            run = {"k": k, "seed": seed, "minimum_size": int(count.min()), "maximum_share": float(count.max() / len(labels)),
                   "iterations": int(model.n_iter_), "inertia": float(model.inertia_), "seconds": time.monotonic() - start,
                   "failures": failures, "passed": not failures}
            runs.append(run)
            print("FIT", json.dumps(run), flush=True)
            del model
            gc.collect()
        ari = [float(adjusted_rand_score(labels_for_k[a], labels_for_k[b])) for a, b in [(0, 1), (0, 2), (1, 2)]]
        kvals.append({"k": k, "ari_pairs": ari, "minimum_ari": min(ari), "passed": bool(passed and min(ari) >= cfg["min_ari"])})
    passing = [row["k"] for row in kvals if row["passed"]]
    chosen = min(passing) if passing else None
    write_json(d / "selection.json", {"selected_k": chosen, "status": "GROUPS_READY" if chosen else "GROUPING_NOT_READY", "k_results": kvals, "runs": runs})
    pd.DataFrame(runs).to_csv(d / "runs.csv", index=False)
    if chosen is None:
        print("GROUPING_NOT_READY", json.dumps(kvals), flush=True)
        return {"status": "GROUPING_NOT_READY", "selected_k": None}
    with np.load(d / f"k{chosen}-seed{cfg['seeds'][0]}.npz") as model:
        centers, labels = model["centers"], model["labels"]
    radius, boundary, rp, bp = geometry(x, centers, labels)
    full_labels = np.full(len(ids), -1, int)
    full_labels[valid] = labels
    np.savez_compressed(d / "immutable-model.npz", centers=centers, movie_ids=ids[valid], catalog_movie_ids=ids, labels=labels,
                        radius=radius, boundary=boundary, radius_percentile=rp, boundary_percentile=bp)
    frame = pd.DataFrame({"movie_id": ids, "group_id": full_labels})
    for key, values in [("radius", radius), ("boundary", boundary), ("radius_percentile", rp), ("boundary_percentile", bp)]:
        frame[key] = np.nan
        frame.loc[valid, key] = values
    frame.to_parquet(d / "assignments.parquet", index=False)
    movies = pd.read_parquet(p / "movies.parquet")
    vocab, names = read_json(p / "vocabulary.json"), read_json(p / "names.json")
    feature_ids = ["genre:" + str(g) for g in vocab["genre_ids"]] + ["keyword:" + str(t) for t in vocab["keyword_ids"]]
    groups = []
    valid_rows = np.flatnonzero(valid)
    for g in range(chosen):
        ix = np.flatnonzero(labels == g)
        ranked = ix[np.lexsort((ids[valid][ix], radius[ix]))][:5]
        top = np.argsort(-centers[g], kind="stable")[:12]
        groups.append({"group_id": g, "movies": len(ix),
                       "features": [{"id": feature_ids[j], "name": names.get(feature_ids[j], feature_ids[j]), "center_weight": float(centers[g, j])} for j in top],
                       "center_examples": [{"movie_id": int(ids[valid][j]), "title": str(movies.title.iloc[valid_rows[j]])} for j in ranked]})
    write_json(d / "groups.json", groups)
    bound_artifacts = {str(path.relative_to(OUT)): pin(path) for path in
                       [p / "features.npz", p / "vocabulary.json", p / "axis.npz", d / "immutable-model.npz", d / "assignments.parquet"]}
    version = hashlib.sha256(json.dumps(bound_artifacts, sort_keys=True).encode()).hexdigest()
    write_json(d / "model-version.json", {"model_version": version, "artifacts": bound_artifacts,
                                         "append_refits_allowed": False, "reference_population_changes_allowed": False})
    print("GROUPS_READY", chosen, flush=True)
    return {"status": "GROUPS_READY", "selected_k": chosen}


def profiles(cfg):
    verify_stage("prepare")
    result = verify_stage("fit")
    require(result["status"] == "GROUPS_READY", "grouping gate must pass")
    p, f, d = OUT / "prepare", OUT / "fit", OUT / "profiles"
    x = sparse.load_npz(p / "features.npz")
    movies = pd.read_parquet(p / "movies.parquet")
    assignment = pd.read_parquet(f / "assignments.parquet")
    labels = assignment.group_id.to_numpy(int)
    with np.load(f / "immutable-model.npz") as model:
        centers = model["centers"]
    rp, bp = assignment.radius_percentile.to_numpy(), assignment.boundary_percentile.to_numpy()
    rows, users = [], []
    for c in read_json(p / "contexts.json"):
        start = time.monotonic()
        comparison = compare_user_groups(x, centers, labels, c["viewed"], c["oi"], c["stars"])
        available = movies.available.to_numpy(bool).copy()
        available[np.asarray(c["viewed"], int)] = False
        totals = {"M1": 0, "M2A": 0}
        distance_pairs = 0
        for g in range(len(centers)):
            row = {"uid": c["uid"], "group_id": g, "experience": int(comparison["experience"][g]),
                   "affinity": float(comparison["affinity"][g]), "positive_group": bool(comparison["positive_group"][g]),
                   "eligible_m1": bool(comparison["eligible_m1"][g]), "eligible_m2a": bool(comparison["eligible_m2a"][g]),
                   "selected_m1": bool(g in comparison["selected_m1"]), "selected_m2a": bool(g in comparison["selected_m2a"]),
                   "m1_geometry_candidates": None, "m2a_geometry_candidates": None}
            ix = np.flatnonzero(labels == g)
            legal = available[ix] & (rp[ix] <= .99)
            if row["selected_m1"]:
                row["m1_geometry_candidates"] = int(legal.sum())
                totals["M1"] += int(legal.sum())
            if row["selected_m2a"]:
                da = anchor_distance(x[ix], x[comparison["positives"]])
                ra = midrank(da)  # Fixed initial I_g, prior to availability/seen filters.
                count = int(np.sum(legal & (ra >= .75) & (bp[ix] <= .20)))
                row["m2a_geometry_candidates"] = count
                totals["M2A"] += count
                distance_pairs += len(ix) * len(comparison["positives"])
            rows.append(row)
        users.append({"uid": c["uid"], "h": c["h"], "positive_inputs": len(comparison["positives"]),
                      "missing_viewed_geometry": comparison["missing_viewed_geometry"],
                      "m1_groups": len(comparison["selected_m1"]), "m2a_groups": len(comparison["selected_m2a"]),
                      "m1_candidates": totals["M1"], "m2a_candidates": totals["M2A"],
                      "anchor_distance_pairs": distance_pairs, "seconds": time.monotonic() - start})
    pd.DataFrame(rows).to_parquet(d / "user-groups.parquet", index=False)
    user_frame = pd.DataFrame(users)
    user_frame.to_csv(d / "users.csv", index=False)
    report = {"users": len(users), "nonempty_inputs": int(sum(user_frame.h > 0)),
              "users_with_m1_geometry": int(sum(user_frame.m1_candidates > 0)),
              "users_with_m2a_geometry": int(sum(user_frame.m2a_candidates > 0)),
              "future_rating_values_read": 0, "predicted_ratings_read": 0,
              "note": "Geometry availability only, not final recommendations or preference quality."}
    write_json(d / "report.json", report)
    print("PROFILES", json.dumps(report), flush=True)
    return {"status": "GEOMETRY_PREPARATION_COMPLETE"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "fit", "profiles"])
    stage = parser.parse_args().stage
    approved_fingerprint = approved(stage)
    cfg = read_json(DOC / "config.json")
    guard = Guard(cfg, stage)
    guard.start()
    directory = OUT / stage
    require(not directory.exists(), "preserve previous stage directory")
    directory.mkdir(parents=True)
    try:
        parent_stages = {"prepare": [], "fit": ["prepare"], "profiles": ["prepare", "fit"]}[stage]
        parent_pins = {}
        for parent in parent_stages:
            verify_stage(parent)
            parent_pins[parent] = {"seal": pin(OUT / parent / "seal.json"), "review": pin(DOC / (parent + "-result-review.json"))}
        with threadpool_limits(limits=cfg["threads"]):
            extra = {"prepare": prepare, "fit": fit, "profiles": profiles}[stage](cfg)
        require(fingerprint() == approved_fingerprint, "code or design changed during execution")
        if stage == "prepare":
            base = Path(cfg["source_root"]) / "outputs/recommendation-evidence"
            for name, expected in cfg["sources"].items():
                require(pin(base / name) == expected, "source changed during prepare " + name)
        for parent in parent_stages:
            verify_stage(parent)
            require(parent_pins[parent] == {"seal": pin(OUT / parent / "seal.json"),
                                          "review": pin(DOC / (parent + "-result-review.json"))}, "parent changed during execution")
        write_json(directory / "resource.json", guard.result())
        seal(stage, {**extra, "parents": parent_pins}, approved_fingerprint)
    except Exception as exc:
        write_json(directory / "failure.json", {"type": type(exc).__name__, "message": str(exc), **guard.result()})
        raise
    finally:
        guard.done.set()


if __name__ == "__main__":
    main()
